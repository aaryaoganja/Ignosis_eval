# Experiment protocol

This document describes how a reliability run is executed, stored and scored, and what may be
called a result.

## What counts as a result

A number is a **measured result** only if **all** of the following hold:

1. the evaluation profile is not a placeholder (`profile.status != placeholder`);
2. the benchmark and gold are real, versioned and frozen (not `tests/fixtures/…`);
3. the evaluator ran on a real model backend (not `mock` / `none`, except that K0 is a legitimate
   no-LLM baseline);
4. the run is `official` (clean git tree, full split, frozen gold) and its `completion.json` says
   `completed`;
5. the metric definitions have been reconciled with the Stage 4 specification
   (`provisional_definitions` is false).

None of these holds today. `metrics.json` lists each violated condition under `warnings`.

## Run lifecycle (`runner/experiment.py`)

| Step | What happens | Fails closed when |
|---|---|---|
| 1 | Load profile, hash it | the file is invalid |
| 2 | `verify_benchmark`: every dataset, audio and card hash vs the manifest | any drift, missing/extra file, missing metadata |
| 3 | `verify_gold`: every gold hash vs the gold manifest; gold bound to this benchmark manifest | any drift, missing manifest, wrong binding |
| 4 | `check_benchmark --require-gold`: pairs, splits, leakage, holdout registry | any error |
| 5 | Preconditions | holdout without `--confirm-holdout`; official with a dirty tree, placeholder profile or item subset |
| 6 | Write `manifest.json` **before** any evaluation | missing required metadata (schema) |
| 7 | For each item × rep: normalize (pipeline ASR) → evaluate **inside the gold-access guard** → re-validate the contract → attach experiment metadata → write artifacts | the evaluator touches a protected path or spawns a process: the run is marked failed |
| 8 | Re-verify benchmark and gold hashes | anything changed during the run: the run is marked failed |
| 9 | Write `completion.json`, append to `runs/LEDGER.jsonl`, seal the run read-only | — |
| 10 | Score (unless `--no-score`) | see scoring |

Evaluator exceptions other than guard violations are recorded in `errors.json`. The run continues,
no record is written, and the scorer counts the item-rep as an integrity failure (`missing_record`).

## Run manifest (`contracts/run_manifest.py`)

It stores `run_id`, `created_at`, `purpose`, and `official`. It also stores:

- **dataset**: name, version, split, benchmark-manifest SHA-256, dataset hash, split hash, and item ids
  in execution order.
- **gold**: version, gold-manifest SHA-256, gold hash, split hash.
- **rubric_version**.
- **profile**: id, version, rubric version, SHA-256, path, status.
- **evaluator**: name, version, architecture, backend, model id, temperature, top_p, max tokens,
  **prompt hashes**, **retry policy**, options, config hash.
- **component_versions**: package, scorer, metric definitions, schema versions, evaluator.
- **git**: commit, branch, dirty.
- **asr**: engine, model, version, config.
- **audio_rendering**: the number of items with audio and the distinct rendering configs.
- **repetitions**.
- **randomization**: seed, item order, rep-seed derivation `sha256(f"{seed}:{item_id}:{rep}")`.
- **environment** and **invocation**.

The schema itself rejects a manifest that is missing any required field. It also rejects an
LLM-backed evaluator without a model id and temperature, and an official run with a dirty tree or no
gold.

## Storage (append-only)

```
runs/<run_id>/manifest.json
runs/<run_id>/<item_id>/rep_<k>/normalized_input.json
                                llm_requests.jsonl      every request (payload logged as SHA-256)
                                llm_responses.jsonl     every attempt, incl. failed/retried ones
                                evaluation_record.json  absent if the evaluator failed
                                timing.json  usage.json  errors.json
runs/<run_id>/completion.json
runs/LEDGER.jsonl                                       append-only journal of starts/finishes
scoring/<run_id>[--<suffix>]/item_scores.csv  metrics.json  discordance_tables.csv  scoring_manifest.json
```

Rules:
- Run ids are timestamp + evaluator + split + a random suffix, and a run directory is created with
  `exist_ok=False`.
- Every file is opened with O_EXCL and then made read-only, and the finished run is sealed.
- Scoring output directories must not already exist. Use `ignosis-eval score --suffix <tag>` to
  re-score (for example with a new interval policy). Scoring always re-verifies the gold, benchmark
  and profile hashes recorded in the run manifest.

`runs/` and `scoring/` are git-ignored. Archiving official runs (for example to object storage keyed
by run id) is a pending decision.

## Scoring (`scoring/`)

The loader verifies before it reads. It checks, in order: the run manifest is valid; the completion
status is `completed` (unless `--allow-incomplete`, which is recorded); the benchmark and gold hashes
equal the recorded ones; the profile hash equals the recorded one; every item is in the split and has
frozen gold. Only then are records read, as untrusted data.

Outputs:
- `item_scores.csv`: one row per item × rep, with verdicts, detection counts, evidence counts,
  integrity/modality codes, DW/CL flags, and the record content hash.
- `metrics.json`: every metric (see `reliability.md`), slices, language delta, counts, the interval
  policy and warnings.
- `discordance_tables.csv`: one row per disagreement. Types include: missed_fail, unsupported_pass,
  unwarranted_abstention, wrong_abstention_type, missed_gate_fail, critical_false_positive,
  unsupported_gate_pass, critical/major/minor miss, unsupported_defect, attribution_mismatch,
  unjustified_attribution, incomplete_evidence, unfaithful_evidence, dangerous_win/clean_loss
  mismatch, integrity_violation and modality_violation.
- `scoring_manifest.json`: scorer version, definitions version, all input hashes, and the config.

## Holdout discipline

- Develop and debug only on `dev` (and `redteam` / `calibration` as designed).
- A holdout run needs `--confirm-holdout` and is journaled in the ledger. It must not be followed by
  changes to evaluator code or prompts that are then re-evaluated on the same holdout version.
- The holdout registry is append-only. A registered case can never move to another split, change
  content, or have its content reused elsewhere.
- Holdout gold must be labelled blind to evaluator outputs (enforced by the schema).

## Reproducibility

Given the same code commit, benchmark, gold, profile, evaluator configuration, seed and repetitions,
the normalized inputs, LLM request logs and Evaluation Records (excluding runner timestamps) are
byte-identical across runs. This is tested with the noisy mock backend in
`tests/test_runner.py::test_run_reproducibility`. Real LLM backends are not deterministic even at
temperature 0. For them, reproducibility means the recorded manifest reconstructs the configuration,
and consistency metrics quantify the residual variance.

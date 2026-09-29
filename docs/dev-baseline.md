# DEV draft baseline

**DEV ENGINEERING MEASUREMENT — NOT FINAL RELIABILITY EVIDENCE. FINAL RELIABILITY VALIDATION: PENDING.**

The first DEV measurements of the evaluator, run on the 25 DEV transcript drafts in `bench/dev/transcripts/`.

**What this is not.**
- It is not an experiment-protocol run. The drafts are not hash-listed and there is no gold.
- It is not reliability evidence. The drafts are pending native human review (`provenance.yaml`,
  `human_review_pending: true`).
- The reference is the **frozen DEV design intent** (`bench/public`: blueprint gates, findings, verdicts, controls,
  pairs), read in memory. Nothing is written as, or converted into, a gold label (CLAUDE.md invariant 2 / 9).
- When the evaluator disagrees with the intent, the disagreement is reported and classified. The intent is never
  edited.

## Status (2026-09-29)

| Component | Status |
|---|---|
| Evaluator provider | **Gemini**, `gemini-3.8-flash` (owner decision, 2026-09-29), family `google-gemini`, which differs from the drafts' assisting family (authoring constraint 1). Settings live in `evaluators/provider_config.py`: temperature 0, seed 0, JSON mode + contract validation + 1 schema retry, ≤ 3 transport retries, 120 s timeout, TRANSCRIPT input. The locked-run pin stays B-05. |
| Provider client | `evaluators/llm.py::GeminiClient` (generateContent, stdlib HTTPS). The key comes only from the runtime env `GEMINI_API_KEY`. Tested against a local fake server. **`GEMINI_API_KEY` is not set in the environment where this baseline was produced**, so no real model call has been made. |
| A | Implemented (single structured call, schema validation plus 1 retry, front-end merge only). **Not executed: GEMINI_API_KEY missing.** |
| A+ | Implemented (the 8 deterministic steps on A's stored raw output). **Not executed: needs A's output.** |
| B | Implemented end to end: extraction, verifier, `SpecRuleEngine` (every MVP gate and code), one batched judgment call, finalize; lists `promise_of_action` events as unverified commitments (EXE-03). **Not executed: GEMINI_API_KEY missing.** |
| K0 | Executed. Its reviewed lexicon terms are empty (B-04), so it is a degenerate floor: it fires only the deterministic pre-checks. |
| Front end | Executed on the 18 scored drafts: evaluability and G7 agree with the design on every item. |
| Consistency harness | Executed (`dev consistency`). The front-end input hashes and the K0 records are identical across repetitions. |

Items excluded from the call-level baseline:
- the six snippets (SN-D01..D06) are component tests of the normalizer, which needs the B-04 lexicon;
- G-02-N5 is a tuning-only audio copy; its audio comes after B-06 / B-09.

## Commands

```bash
ignosis-eval dev run --systems K0                                   # runs now (deterministic)
ignosis-eval dev run --systems K0,A,A+,B [--reps k]                  # gemini by default; needs GEMINI_API_KEY
ignosis-eval dev report --run-id <id> [--price-in USD --price-out USD]   # reports/dev-baseline/*.json|md
ignosis-eval dev consistency --reps 3 --systems K0 [--items ...]        # reproducibility smoke run
```

A run without the key stops before writing anything: `PROVIDER NOT CONFIGURED: GEMINI_API_KEY is not set ...`.
Provider failures during a run never become verdicts:
- a transport failure after retries, a schema-invalid or empty output (twice), or a blocked prompt gives an
  EVALUATION_FAILED record;
- HTTP 401 / 403 / 404 or an invalid key stops the run (fail closed).

Records are
written once under `dev_draft_runs/<run_id>/`, which is git-ignored like `runs/`:
- normalized input, record, LLM traffic, timing, usage and errors per (system, item, rep);
- B's and A+'s derivation logs;
- `draft_run.json` with the configuration, versions, prompt hashes, front-end results and exclusions.

## To complete the baseline (the only missing input is the key)

1. Set `GEMINI_API_KEY` (and optionally `GEMINI_MODEL`) in the runtime environment. Never write the key to a file
   in the repository.
2. Run `ignosis-eval dev smoke`. It passes one synthetic demo call through A, A+ and B and checks that:
   - the request succeeds and the response parses;
   - the extraction and judgment schemas parse;
   - the records validate;
   - no failure became a verdict.
   Then run `ignosis-eval dev run --systems K0,A,A+,B`. `scripts/dev_gemini_baseline.sh` does steps 2 and 3 in one
   go.
3. Run `ignosis-eval dev report --run-id <id>`. Add `--price-in/--price-out` (USD per million tokens, from the
   current Gemini price list) for a cost estimate; the price snapshot is B-08.
4. For LLM run-to-run stability, run `ignosis-eval dev consistency --systems K0,A,A+,B --reps 3` and pass its
   `--consistency-run-id` to the report.

Do not tune prompts on these outcomes: the prompts are unoptimized stubs (P-2; the tuning protocol is P-7).

## Metrics (`devbaseline/metrics.py`)

Reference per scored item: the design intent. Every value is a number with its numerator and denominator, or
`UNMEASURED` (no support in the run).

| Metric | Definition |
|---|---|
| Verdict accuracy | majority verdict = intended verdict |
| Critical recall | (item, gate) intended fired (FAIL, or INCONCLUSIVE with trigger) → majority FAIL |
| Must-not-fire precision | control (item, gate) targets (K-01 G1, K-07 G4, MD-C1 G5, MD-C2 G6) → majority not FAIL |
| Gate false fires | gates fired where the intent has none (listed) |
| Defect precision / recall | intended non-gate codes vs majority-emitted ASSERTED codes, at code level. Beat anchors are not mapped to turns before the freeze, so there is no anchor matching. |
| Evidence faithfulness | cited evidence (fired gates, ASSERTED findings) that verifies against the unit's normalized input: the turn exists, the role matches, and `quote_score` ≥ `quote_match_min`. Needs no reference. |
| Abstention | EVALUATION_FAILED, NOT_EVALUABLE and PARTIAL counts; evaluability agreement; INCONCLUSIVE checks by code |
| Attribution agreement | detected intended findings and gates: record attribution = design `attribution (T)` |
| Dangerous-win agreement | majority DW tag = intended DW |
| Clean Loss agreement | majority Clean Loss tag = the blueprint's `clean_loss` |
| Pair accuracy | SD-20 on MP-01 (COM-02), MP-04 (G1), MP-10 (G4). A gate target counts as correct when it fired on the violating member and not on the clean one; a code target when it was emitted on the violating member and not on the clean one. Inversions are counted. |
| Consistency | only with k > 1: verdict-stable items, and record content-hash-stable items |
| Latency | p50 / p95, nearest rank, per (unit, rep) |
| Cost | token sums; USD only from user-supplied prices (B-08 pending) |

Disagreement classes:

| Class | Meaning |
|---|---|
| `evaluator_failure` | An intended defect was missed, or something unintended was reported, on an item whose design label is Sure. |
| `benchmark_ambiguity` | The design label is Probable or Unsure, contested, or carries an ambiguity note. |
| `unsupported_or_oos` | The system reported the check INCONCLUSIVE or OUT_OF_SCOPE, or its gate evidence did not verify. |
| `agent_behavior_error_detected` | An intended agent defect that was found. |

## What B cannot yet decide (reported INCONCLUSIVE, never PASS)

| Check | Why | Pending |
|---|---|---|
| RES-11 | `loop_similarity` is `PENDING_HUMAN_SIGNOFF` | B-11 |
| UND-12 | Extraction 2.0.0 carries no slot per ask. INCONCLUSIVE only when an ask follows a stated commitment. | — |
| UND-03, COM-05 | Read-back values and constraint dates arrive as raw text. Digits are compared; number words need the numerals lexicon. | B-04 |
| TRT-03 | Only when the extraction omits `turn_languages` | — |
| PLT-02 / PLT-03 / PLT-04 | Only in audio / platform-ASR units | B-11 / B-06 |

Every B record is therefore PARTIAL (V7: an in-scope check is INCONCLUSIVE); the verdict is unaffected (V7). The
conventions are in `spec-reconciliation.md` §3.46–3.47.

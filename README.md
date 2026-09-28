# Ignosis Voice AI Quality Evaluator: reliability-experiment infrastructure

This repository holds the infrastructure for a pre-registered reliability experiment on a quality evaluator for
collections voice-AI calls. It implements the **frozen specification pack** in [`docs/spec/`](docs/spec/):

- contract `1.0.0-frozen`;
- rubric `1.0-mvp`;
- profile `collections_default_v1`;
- experiment protocol;
- scoring specification;
- implementation blockers.

How the implementation maps to the spec is in [`docs/spec-reconciliation.md`](docs/spec-reconciliation.md).

> **Nothing in this repository is a measured result.** The benchmark is empty (B-01/B-02 pending). The LLM
> backend is a replay mock (B-05), the ASR is a cache replay (B-06), the lexicon terms are empty (B-04) and 17
> profile/rubric values are `PENDING_HUMAN_SIGNOFF`. Every locked (holdout / red-team) run is refused until those
> are resolved. Every `metrics.json` carries the SD-29 scope line and warnings.

## Status at a glance

| Layer | Status |
|---|---|
| Spec pack | `docs/spec/` (6 files, verbatim). `ignosis-eval spec pending` lists the pending items. |
| Contracts, front end, engine, K0 / A / A+ / B interfaces, gold derivation, scorer, runner, lock, blinding | Implemented and tested (`python -m pytest`). |
| B rule engine, deterministic normalizer, DC-02 / DC-LANG / DC-01-audio, timing signals | Next phase / pending sign-off (see the reconciliation doc §5). |
| Benchmark content, gold, registries | **Empty.** Authored by humans (B-01..B-03). |
| Results | **None.** |

## Quick start

```bash
pip install -e ".[dev]"
python -m pytest                         # the whole suite
ruff check src tests scripts && mypy     # lint + types
ignosis-eval spec check && ignosis-eval spec pending
ignosis-eval bench check --scope dev --require-gold
```

A full dev run once items, cards and gold exist (plumbing only while the backend is a mock):

```bash
ignosis-eval bench manifest --scope dev --dataset-version v1 --created-by <id>
ignosis-eval gold freeze    --scope dev --gold-version g1 --frozen-by <id> --labeling-protocol-version <v>
ignosis-eval run   --split dev --systems K0,A,A+,B --base-seed 1234 --replay-dir <replay fixtures>
ignosis-eval blind --run-id <run_id>
ignosis-eval score --run-id <run_id>     # -> scoring/<run_id>/{item_scores.csv, metrics.json, discordance_tables.csv, human_checks.csv}
```

## Repository layout

```
docs/spec/          frozen specification pack (authoritative)
bench/              dev benchmark (empty scaffolding), hash lists, registries, card template
src/ignosis_eval/
  spec/             spec loader, check registry from rubric.yaml, PENDING inventory
  contracts/        NormalizedInput, EvaluationRecord, GoldLabel, CaseCard, ItemMeta, Registries, manifests
  pipeline/         shared front end: transcript intake, ASR cache, normalization, lexicon, pre-checks, evaluability
  engine/           deterministic verifier, confidence ceiling, attribution, verdict engine, tags, routing
  evaluators/       K0, A, A+ (derived), B (interfaces), replay mock, prompt stubs + generated rubric section
  golddrv/          independent mode-level gold derivation (capability table, attribution)
  metrics/ scoring/ stats/   scoring-spec SD-01..SD-31 on the blinded view
  benchmark/ integrity/      layout, card rules, bench checks, locked-run registry; hash lists, gold freeze, guard
  runner/           P-5/P-6 run protocol, lock (P-8), blinding (P-10), append-only storage (P-11)
tests/              synthetic-stub tests only (P-1 rule 3)
```

## Documentation

- [`docs/spec-reconciliation.md`](docs/spec-reconciliation.md): what changed and why; conventions; open questions.
- [`docs/data-contracts.md`](docs/data-contracts.md), [`docs/benchmark-authoring.md`](docs/benchmark-authoring.md),
  [`docs/experiment-protocol.md`](docs/experiment-protocol.md), [`docs/reliability.md`](docs/reliability.md):
  implementation maps to the spec.
- [`docs/gap-analysis.md`](docs/gap-analysis.md): history of the infrastructure phase and its reconciliation.

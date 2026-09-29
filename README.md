# Ignosis Voice AI Quality Evaluator: reliability-experiment infrastructure

This repository holds the infrastructure for a pre-registered reliability experiment on a quality evaluator for
collections voice-AI calls. It implements the **frozen specification pack** in [`docs/spec/`](docs/spec/):

- contract `1.2.0-frozen` (AJ-01..AJ-12 plus the Stage-5 adjudications SC-01..SC-08, BD-01, BD-02);
- rubric `1.2-mvp`;
- profile `collections_default_v1` `1.1.1`;
- experiment protocol;
- scoring specification;
- implementation blockers.

How the implementation maps to the spec is in [`docs/spec-reconciliation.md`](docs/spec-reconciliation.md). The
freeze commitments of bench-a1 design v1.0 (13 file hashes, the private bundle commitment and the Stage-5
adjudication log) are in [`docs/freeze/`](docs/freeze/). The earlier 1.1 adjudication record is kept as history in
[`docs/history/final-adjudication-1.1.md`](docs/history/final-adjudication-1.1.md).

> **Nothing in this repository is a reliability result.** The benchmark design is frozen. The 25 DEV transcripts
> exist as unfrozen drafts in `bench/dev/transcripts/` (Claude-assisted; human review pending). The first DEV
> baseline (`reports/dev-baseline/`, `docs/dev-baseline.md`) compares against the design intent, not gold. Holdout
> transcripts, audio and all gold do not exist yet (B-01/B-02 pending). bench-a1 contains no G7 positive, so G7 recall is not measured (BD-02). The LLM
> backend is a replay mock (B-05), the ASR is a cache replay (B-06), the lexicon terms are empty (B-04) and 18
> profile/rubric values are `PENDING_HUMAN_SIGNOFF`. Every locked (holdout / red-team) run is refused until those
> are resolved. Every `metrics.json` carries the SD-29 scope line and warnings.

## Status at a glance

| Layer | Status |
|---|---|
| Spec pack | `docs/spec/` (the 6 frozen 1.2.0 files, verbatim; hashes committed in `docs/freeze/FREEZE-HANDOFF.md` and verified by every `bench check`). `ignosis-eval spec pending` lists the pending items. |
| Contracts, front end, engine, K0 / A / A+ / B interfaces, gold derivation, scorer, runner, lock, blinding | Implemented and tested (`python -m pytest`). |
| B: extraction contract + verifier, full rule engine (`SpecRuleEngine`: every MVP gate and code, batched judgments) | Implemented and tested; B's default. A, A+ and B run end to end (tested on replay fixtures and a fake OpenAI-compatible server). |
| Evaluator provider | **PENDING (B-05).** `openai` client implemented (stdlib), inert without `OPENAI_API_KEY` and a pinned snapshot; a Claude evaluator is refused on the Claude-assisted drafts (constraint 1). |
| Opaque unit aliases (P-17, SC-05) | Random `u_xxxxxxxx` alias per unit per run, private mapping, audio renamed before ASR, pre-run identity-leak test that fails the run (identity tokens only; ordinary words pass). |
| Deterministic normalizer, DC-02 / DC-LANG / DC-01-audio, diarization turn threshold, timing signals | Pending sign-off (see the reconciliation doc §5). |
| Frozen DEV design (Stage 5) | `bench/public/`: 25 DEV case cards (functional beats), master matrix and gold blueprint, verbatim; committed by `docs/freeze/FREEZE-public.md`; validated by `ignosis-eval bench public-check` (0 errors, 6 PD015 authoring-rule warnings; clarifications BD-03..BD-05 in `docs/bd-changelog.md`). DEV only. |
| Transcripts, gold, registries | DEV transcript **drafts** in `bench/dev/transcripts/` (25; not frozen; `bench transcript-qc` clean). Gold and registries are empty (B-02); the blueprint is intent, not gold. |
| Results | **None.** |

## Quick start

```bash
pip install -e ".[dev]"
python -m pytest                         # the whole suite
ruff check src tests scripts && mypy     # lint + types
ignosis-eval spec check && ignosis-eval spec pending
ignosis-eval bench check --scope dev --require-gold
ignosis-eval bench transcript-qc <dir>          # draft DEV transcripts (<ITEM_ID>.txt) before the hash freeze
ignosis-eval dev run --systems K0 && ignosis-eval dev report --run-id <id>   # DEV draft baseline (docs/dev-baseline.md)
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
docs/spec/          frozen specification pack (authoritative, 1.2.0-frozen)
docs/freeze/        freeze commitments (hashes, private bundle commitment, Stage-5 adjudication log)
bench/              frozen DEV design (public/), dev items (empty), hash lists, registries, card template
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

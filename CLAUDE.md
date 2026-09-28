# CLAUDE.md: working rules for this repository

This repository is **reliability-experiment infrastructure** for the Ignosis Voice AI Quality Evaluator
(collections). Read `docs/gap-analysis.md` first. It lists what is provisional and what still needs
human input.

## Invariants (never break these)

1. **The evaluator never sees gold.** Evaluators receive only the normalized Canonical Input and an
   `EvaluationContext`. They must not import `contracts.gold_label`, `integrity.freeze`, `benchmark`,
   `scoring`, `metrics` or `runner` (enforced by `tests/test_architecture_boundaries.py`). At runtime
   they run inside `ProtectedPathGuard`, which blocks any read or write of `gold/`, `case_cards/` or
   `manifests/`, and blocks subprocesses.
2. **Gold is never derived from evaluator output.** `GoldProvenance.derived_from_evaluator_output` is
   the literal `false`. Holdout gold must be labelled blind to evaluator outputs. Never write code that
   turns records into gold labels, including "pre-filled" gold.
3. **The scorer is independent of the evaluator.** `scoring/` and `metrics/` may use only the declared
   contracts, `stats` and `integrity` (verification). Do not add evaluator-specific assumptions.
4. **Fail closed.** Any hash drift, missing manifest, missing metadata or version reuse must raise.
   Never add a flag that skips verification by default.
5. **Append-only results.** Run and scoring files are write-once (`'x'` mode, then read-only). To
   re-score, use `--suffix`. Never overwrite or delete a run.
6. **Never present placeholders as results.** The profile in `config/profiles/` is a placeholder. Mock
   backend outputs and the `tests/fixtures/benchmark_smoke` data measure plumbing only. Keep the
   warnings in `metrics.json`.
7. **Holdout discipline.** Never use the holdout split for tuning or debugging. Holdout runs need
   `--confirm-holdout`, and the holdout registry is append-only.
8. **No secrets and no real call audio in git.** Audio is git-ignored except tiny synthetic test
   fixtures. Real (non-synthetic) cases need the `pii_reviewed` tag.

## Current phase boundary (STOP conditions)

The infrastructure phase is complete. **Do not**, without an explicit request:
- optimize or rewrite evaluator prompts (`evaluators/prompts/*.md` are marked unoptimized stubs);
- tune A / A+ / B, or wire the real LLM backend (`AnthropicLLMClient` deliberately raises);
- author final benchmark cases or gold labels;
- build a UI.

## Where things live

| Concern | Location |
|---|---|
| Contract schemas + versions | `src/ignosis_eval/contracts/`, `src/ignosis_eval/versions.py`, `schemas/` |
| Provisional vocabularies | `contracts/enums.py` |
| Placeholder taxonomy + capability map | `config/profiles/collections_placeholder.yaml` |
| Metric definitions | `metrics/definitions.py` (registry `METRICS`) + `docs/reliability.md` |
| Interval policy | `stats/proportion.py` (`IntervalPolicy`) and `stats/intervals.py` |
| Evaluator aggregation (gate precedence, routing, DW/CL) | `evaluators/builder.py` |
| Benchmark rules | `benchmark/case_card_rules.py` (CC/CX/CP/CS) and `benchmark/checks.py` (B0xx) |
| Freeze / verify | `integrity/freeze.py` |
| Run lifecycle | `runner/experiment.py` |

## Commands

```bash
pip install -e ".[dev]"
python -m pytest                     # must pass before every commit
ruff check src tests scripts         # lint
ignosis-eval schemas export          # after any contract change (a test enforces sync)
ignosis-eval benchmark check --benchmark-root benchmark --require-gold
ignosis-eval manifest build|verify / gold freeze|verify / run / score   (see README)
```

## Change rules

- A contract change bumps its version in `versions.py`, re-exports `schemas/`, and updates
  `docs/data-contracts.md`.
- A metric-definition change bumps `METRIC_DEFINITIONS_VERSION`, and updates `docs/reliability.md` and
  the handcrafted-output tests in `tests/test_metrics.py`.
- Reconciling a provisional item (G1–G14) updates `docs/gap-analysis.md` in the same commit.
- Keep commits small, run the tests before each commit, and never push a failing commit.

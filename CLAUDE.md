# CLAUDE.md: working rules for this repository

This repository is **reliability-experiment infrastructure** for the Ignosis Voice AI Quality Evaluator
(collections). The authority is the frozen specification pack in `docs/spec/` (contract `1.0.0-frozen`). Read
`docs/spec-reconciliation.md` first: it maps the code to the spec and lists the conventions, the open questions
and what is still pending.

## Invariants (never break these)

1. **The evaluator never sees gold.**
   - Systems (K0/A/A+/B) receive only the `NormalizedInput` and an `EvaluationContext`.
   - The evaluator side (`evaluators/`, `engine/`, `pipeline/`) must not import `contracts.gold_label`,
     `contracts.case_card`, `integrity.freeze`, `benchmark`, `scoring`, `metrics`, `golddrv` or `runner`.
     This is enforced statically and transitively by `tests/test_architecture_boundaries.py`.
   - At runtime systems run inside `ProtectedPathGuard`, which blocks gold, case cards, manifests, registries
     and the blinding directory, and blocks subprocesses.
2. **Gold is never derived from evaluator output.**
   - `GoldProvenance.derived_from_evaluator_output` is the literal `false`.
   - Holdout and red-team gold is labelled blind.
   - Mode-level gold is derived from content gold by `golddrv/` only (P-12). Never turn records into gold.
3. **The scorer is independent of the evaluator.**
   - `scoring/`, `metrics/`, `golddrv/` and `stats/` use only contracts, `spec`, `integrity` (verification) and
     each other.
   - They read the blinded view only (P-10).
4. **Fail closed.**
   - Any hash drift, missing manifest, missing metadata, version reuse or spec-version mismatch must raise.
   - A `PENDING_HUMAN_SIGNOFF` value is never defaulted: `Spec.threshold()` raises.
   - Never add a flag that skips verification by default.
5. **Append-only results.**
   - Run, view and scoring files are write-once. To re-score, use `--suffix`.
   - Never overwrite or delete a run.
6. **Never present placeholders as results.**
   - Mock-backend outputs, test profiles and synthetic test stubs measure plumbing only.
   - Keep the scope line and warnings in `metrics.json`.
7. **Holdout discipline.**
   - Holdout and red-team data live outside the repo (`$BENCH_PRIVATE_DIR`) and run only as locked runs.
   - A locked run needs `--confirm-holdout`, a tagged clean commit, verified hashes and no blocking pending item
     (P-8 / H7).
   - The locked-run registry is append-only.
8. **No secrets and no real call audio in git.**
   - Audio and ASR caches are git-ignored except tiny synthetic test fixtures.
   - Real (non-synthetic) items need the `pii_reviewed` tag.
9. **No benchmark content by the implementing agent** (P-1 rule 3).
   - Tests use minimal, obviously synthetic stubs.

## Current phase boundary (STOP conditions)

The reconciliation with the frozen spec is complete. **Do not**, without an explicit request:
- optimize or rewrite evaluator prompts (`evaluators/prompts/*.md` are unoptimized stubs);
- tune A / A+ / B, or wire the real LLM backend (`AnthropicLLMClient` raises until B-05);
- author benchmark cases, registries entries, gold labels or red-team items;
- run official (locked) holdout or red-team experiments;
- choose values the spec marks `PENDING_HUMAN_SIGNOFF` (`ignosis-eval spec pending`);
- build a UI.

## Where things live

| Concern | Location |
|---|---|
| Spec pack (authoritative) | `docs/spec/`; loader, registry and pending inventory in `src/ignosis_eval/spec/` |
| Contract schemas + versions | `src/ignosis_eval/contracts/`, `src/ignosis_eval/versions.py`, `schemas/` |
| Front end (intake, ASR cache, pre-checks, evaluability) | `pipeline/` |
| Verifier, confidence, attribution, verdict, tags, routing | `engine/` |
| Systems | `evaluators/` (K0, A, A+ derivation, B interfaces, replay mock) |
| Gold derivation | `golddrv/` |
| Metric definitions | `metrics/` + `docs/reliability.md` (SD map); fixtures in `tests/test_metrics.py` |
| Intervals | `stats/` (SD-26) |
| Benchmark layout, card rules, checks | `benchmark/` (`bench/` on disk) |
| Hash lists / gold freeze / guard | `integrity/` |
| Run protocol, lock, blinding, storage | `runner/` |

## Commands

```bash
pip install -e ".[dev]"
python -m pytest                     # must pass before every commit
ruff check src tests scripts && mypy # lint + types
ignosis-eval schemas export          # after any contract change (a test enforces sync)
ignosis-eval spec pending            # PENDING_HUMAN_SIGNOFF inventory
ignosis-eval bench check --scope dev --require-gold
ignosis-eval bench manifest | gold freeze | run | blind | score | reveal   (see README)
```

## Change rules

- A contract change bumps its version in `versions.py`, re-exports `schemas/`, and updates `docs/data-contracts.md`.
- A metric-definition change needs a spec change first. It bumps `METRIC_DEFINITIONS_VERSION`, and updates
  `docs/reliability.md` and the SD-31 fixtures in `tests/test_metrics.py`.
- A spec-interpretation choice goes into `docs/spec-reconciliation.md` §3, with a test.
- Keep commits small, run the tests before each commit, and never push a failing commit.

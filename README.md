# Ignosis Voice AI Quality Evaluator: reliability-experiment infrastructure

This repository holds the engineering infrastructure for running a rigorous reliability experiment on
a quality evaluator for collections voice-AI calls. It covers typed data contracts, benchmark and
case-card tooling, a gold freeze with fail-closed hash verification, an append-only experiment runner,
and a scorer that is independent of the evaluator and implements executable metric definitions.

> **Nothing in this repository is a measured result.** Evaluators A, A+ and B are interface stubs
> running on a deterministic mock backend. The evaluation profile is a placeholder, and the only
> labelled cases are test fixtures. The metric definitions are provisional until they are reconciled
> with the Stage 4 Reliability Specification, which is not in this repository
> (see [`docs/gap-analysis.md`](docs/gap-analysis.md)).

## Status at a glance

| Layer | What exists | Status |
|---|---|---|
| **Specification** | The Stage 1–4 documents (rubric, taxonomy, Reliability Specification) | **Not in the repository.** Everything that depends on them is listed as provisional in `docs/gap-analysis.md` (G1–G14). |
| **Implementation** | Contracts, benchmark tooling, gold freeze, guard, runner, scorer, metric definitions, evaluator interfaces, 183 tests | Implemented and tested. Definitions are marked `provisional`. |
| **Gold labels** | `benchmark/gold/` | **Empty.** No benchmark gold exists. `tests/fixtures/benchmark_smoke/gold/` holds hand-written *test fixtures*, not benchmark gold. |
| **Evaluator outputs** | `runs/` (git-ignored) | Only mock-backend outputs produced locally. They measure plumbing, not quality. |
| **Measured results** | — | **None.** Every `metrics.json` carries warnings when the profile is a placeholder or the backend is a mock. |

## MVP scope

- Inputs: **audio only**, **transcript only**, or **audio + transcript**. Nothing else.
- Use case: **collections** calls.
- Out of scope: CRM/LMS/account/payment integrations, policy-pack upload, agent/tool traces,
  production APIs, real-time intervention, UI.

## Quick start

```bash
pip install -e ".[dev]"
python -m pytest                                  # 183 tests

# benchmark integrity (the real benchmark is still empty; the smoke fixture has 10 cases)
ignosis-eval benchmark check --benchmark-root benchmark
ignosis-eval benchmark check --benchmark-root tests/fixtures/benchmark_smoke --require-gold
ignosis-eval manifest verify --benchmark-root tests/fixtures/benchmark_smoke
ignosis-eval gold verify     --benchmark-root tests/fixtures/benchmark_smoke

# an end-to-end MOCK experiment on the test fixture (plumbing only, not a result)
ignosis-eval run --benchmark-root tests/fixtures/benchmark_smoke --split dev --evaluator a \
    --reps 3 --seed 1234 --evaluator-option noise_rate=0.2
#   -> runs/<run_id>/...  and  scoring/<run_id>/{item_scores.csv, metrics.json, discordance_tables.csv}

# validate a case card
python scripts/validate_case_cards.py benchmark/templates/case_card.template.yaml   # fails on purpose (placeholders)
```

## How a run works

```
case cards ─┐                         (protected from the evaluator process)
gold ───────┼─ freeze ─> manifests/{benchmark,gold}_manifest.json  (SHA-256, versioned, immutable)
dataset ────┘
               │ verify hashes + benchmark checks (fail closed)
               ▼
dataset/<split>/<case>/input.json ─> normalize (pipeline ASR for audio-only) ─> Evaluator (A | A+ | B | K0)
                                                                                 inside gold-access guard
               ▼
runs/<run_id>/manifest.json + <item>/rep_<k>/{normalized_input, llm_requests, llm_responses,
                                              evaluation_record, timing, usage, errors}   (write-once)
               │ re-verify hashes ─ completion.json ─ seal
               ▼
scorer (gold + Evaluation Records only) ─> scoring/<run_id>/{item_scores.csv, metrics.json,
                                                             discordance_tables.csv, scoring_manifest.json}
```

## Repository layout

```
src/ignosis_eval/
  contracts/     Canonical Input, Evaluation Record, Gold Label, Case Card, Profile, manifests, record checks
  benchmark/     layout, case-card rules, benchmark integrity checks, holdout registry
  integrity/     canonical hashing, manifest build, gold freeze + verification, gold-access guard
  pipeline/      input normalization + ASR interface (mock ASR)
  evaluators/    Evaluator interface, A / A+ / B stubs, K0 keyword floor, mock LLM backend, stub prompts
  stats/         Wilson + Clopper-Pearson intervals, proportion reporting policy
  metrics/       executable metric definitions, alignment, matching, slices, language delta
  scoring/       scorer (loader verifies before reading; outputs write-once)
  runner/        experiment runner, run storage, git provenance
  cli.py         `ignosis-eval` command
benchmark/       dataset/ gold/ case_cards/ {dev,holdout,redteam,calibration}, manifests/, templates/
config/profiles/ placeholder collections profile (NOT the Stage 1-3 rubric)
schemas/         exported JSON Schemas of all contracts (kept in sync by a test)
scripts/         validate_case_cards.py
tests/           183 tests; fixtures/benchmark_smoke = synthetic TEST FIXTURE
docs/            gap-analysis, data-contracts, benchmark-authoring, reliability, experiment-protocol
```

## Documentation

- [`docs/gap-analysis.md`](docs/gap-analysis.md): what existed, what is provisional, what needs human input.
- [`docs/data-contracts.md`](docs/data-contracts.md): every contract, its invariants, and the versioning policy.
- [`docs/benchmark-authoring.md`](docs/benchmark-authoring.md): the case-card workflow, validation rules, and freezing.
- [`docs/reliability.md`](docs/reliability.md): metric definitions as implemented and the interval policy.
- [`docs/experiment-protocol.md`](docs/experiment-protocol.md): the run lifecycle, fail-closed checks, holdout
  discipline, and what counts as a result.
- [`CLAUDE.md`](CLAUDE.md): working rules for AI-assisted development in this repository.

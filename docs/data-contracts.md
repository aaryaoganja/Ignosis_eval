# Data contracts

The frozen documents in [`docs/spec/`](spec/) define the vocabulary. The contracts below implement it as
pydantic v2 models (`extra="forbid"`), versioned in `src/ignosis_eval/versions.py` and exported as JSON Schema
into `schemas/` (`ignosis-eval schemas export`; `tests/test_contracts.py` keeps them in sync).

## Versioning policy

- A contract change bumps its schema string in `versions.py`, re-exports `schemas/` and updates this page.
- The spec-pack versions (`SPEC_CONTRACT_VERSION = 1.0.0-frozen`, `SPEC_RUBRIC_VERSION = 1.0-mvp`,
  `SPEC_PROFILE_ID = collections_default_v1`) are checked at load time. A mismatch fails closed.
- Every enum in `rubric.yaml › enums` is mirrored exactly in `contracts/enums.py`. A drift test enforces it.

| Contract | Schema | Module | Purpose |
|---|---|---|---|
| NormalizedInput | `normalized_input/2.0.0` | `contracts/canonical_input.py` | What a system sees: input and unit mode, the §2.3 header, 1-based turns (evaluation, supplied and ASR text; reliability; role confidence), audio / ASR refs, and the front-end result (evaluability, pre-checks, step status) |
| EvaluationRecord | `evaluation_record/2.0.0` | `contracts/evaluation_record.py` | Output of K0 / A / A+ / B: record status, verdict + critical status + `within_scope_complete`, evaluability, all G1–G9 gate results, findings, explicit check statuses, outcome, tags, routing tier, the always-OOS list, unverified agent commitments |
| GoldLabel | `gold_label/2.0.0` | `contracts/gold_label.py` | Content-level gold per item (SD-01). Mode gold is derived by `golddrv/`, never stored |
| CaseCard | `case_card/2.0.0` | `contracts/case_card.py` | Author intent (B-01) |
| ItemMeta | `item_meta/1.0.0` | `contracts/benchmark.py` | `item.json`: split, pack, language, unit modes, artifacts, synthetic flag |
| Registries | `registries/1.0.0` | `contracts/registries.py` | Controls, pairs, twins (SD-01) |
| BenchManifest / GoldManifest | `bench_manifest/2.0.0`, `gold_manifest/2.0.0` | `contracts/benchmark.py` | Hash lists of a scope (dev or private) |
| RunManifest / RunCompletion | `run_manifest/2.0.0` | `contracts/run_manifest.py` | §17 run artifacts, lock rules, system configs (P-3) |
| BlindViewManifest | `blind_view/1.0.0` | `contracts/run_manifest.py` | P-10 aliased scoring view |
| ScoringManifest | `scoring_manifest/2.0.0` | `contracts/run_manifest.py` | Scorer inputs and output hashes |
| ProfileSpec | (structural) | `contracts/profile.py` | Structural check of `profile.yaml`. Values may be `PENDING_HUMAN_SIGNOFF` |

## Key rules enforced by the schema

- **EvaluationRecord.**
  - `EVALUATION_FAILED` ⇒ verdict null and a failure reason (V0).
  - An `OK` record must contain every gate G1–G9 (SD-02).
  - `critical_status` is present iff the verdict is `CRITICAL_FAIL`, and a gate carries a critical status iff it is `FAIL`.
  - Turn evidence needs turn + quote + role; G7 evidence is `header_field: call_start_ts`.
  - Semantic rules that a raw LLM output can break (H6 capability, H1 external truth) are *measured* by the scorer, not rejected by the schema.
  - `contracts/record_checks.py` rejects unknown codes, gate ids used as findings and NOT_EVALUATED codes.
- **GoldLabel.**
  - Every gate is explicit; INCONCLUSIVE needs `trigger`.
  - Explicit lists are disjoint and contain no gates.
  - A gate FAIL ⇒ `CRITICAL_FAIL`.
  - `derived_from_evaluator_output` is `Literal[False]`.
  - Holdout and red-team labelers must be blind to evaluator outputs.
- **RunManifest.**
  - A+ requires A.
  - Private data only in a locked run.
  - A locked run needs a tag, a clean tree, every hash verified, the canonical profile, no blocking pending item and no mock backend.
  - A tuning run records its round.

# Data contracts

The frozen documents in [`docs/spec/`](spec/) define the vocabulary. The contracts below implement it as
pydantic v2 models (`extra="forbid"`), versioned in `src/ignosis_eval/versions.py` and exported as JSON Schema
into `schemas/` (`ignosis-eval schemas export`; `tests/test_contracts.py` keeps them in sync).

## Versioning policy

- A contract change bumps its schema string in `versions.py`, re-exports `schemas/` and updates this page.
- The spec-pack versions (`SPEC_CONTRACT_VERSION = 1.1.0-frozen`, `SPEC_RUBRIC_VERSION = 1.1-mvp`,
  `SPEC_PROFILE_ID = collections_default_v1`, `SPEC_PROFILE_VERSION = 1.1.0`) are checked at load time. A mismatch
  fails closed.
- Every enum in `rubric.yaml › enums` is mirrored exactly in `contracts/enums.py`. A drift test enforces it.

| Contract | Schema | Module | Purpose |
|---|---|---|---|
| NormalizedInput | `normalized_input/2.1.0` | `contracts/canonical_input.py` | What a system sees: input and unit mode, the §2.3 header, the call-level `role_mapping_confidence` (DC-00, AJ-05), 1-based turns (evaluation, supplied and ASR text; reliability; per-turn `diarization_confidence`), audio / ASR refs, and the front-end result (evaluability, pre-checks, step status) |
| EvaluationRecord | `evaluation_record/2.1.0` | `contracts/evaluation_record.py` | Output of K0 / A / A+ / B: record status, `confidence_source` (SELF_REPORTED for A, COMPUTED otherwise; AJ-06), verdict + critical status + `within_scope_complete`, evaluability (PARTIAL derived, AJ-04), all G1–G9 gate results, findings (TRT-06 carries `measurement_basis`, AJ-02), explicit check statuses, outcome, tags, routing tier, the always-OOS list, unverified agent commitments |
| ExtractionOutput | `extraction/1.0.0` | `contracts/extraction.py` | Evaluator B's extraction (LLM call #1) per `rubric.yaml › extraction_schema` (AJ-07): events with typed per-type fields (`responds_to`, `kind`, `items`, `claims_human`, `offer_type`, `value`/`basis_stated`, `category`/`negated`, `route`, `corrects`/`new_value_id`), identity checks, agent stated values, commitments. JSON Schema in `schemas/extraction.schema.json`; a drift test holds the literals equal to the rubric |
| GoldLabel | `gold_label/2.1.0` | `contracts/gold_label.py` | Content-level gold per item (SD-01). Mode gold is derived by `golddrv/`, never stored. No gold critical status (AJ-12) |
| CaseCard | `case_card/2.1.0` | `contracts/case_card.py` | Author intent (B-01) |
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
  - An `OK` record states its `confidence_source`; `measurement_basis` appears only on TRT-06 findings.
- **ExtractionOutput.**
  - A type-specific field on the wrong event type, or a missing required one, is a schema error.
  - Ids are unique; `component_of` and `correction` references must name existing stated values.
  - `check_vocabulary` checks event types and that `strength` appears on borrower events only.
  - `engine/extraction.py` verifies quotes, and drops (and logs) `responds_to` ids that do not name an earlier
    borrower event or sit on a type outside `responds_to.allowed_on`.
- **GoldLabel.**
  - Every gate is explicit; INCONCLUSIVE needs `trigger`.
  - `REPAIRED` is allowed only on ACC-05 (AJ-08).
  - `outcome.positive` is the labeler's judgement under AJ-09: `PTP_STATED` counts only when firm, for the full or a
    partial amount. There is no firmness field (the adjudication adds none).
  - Explicit lists are disjoint and contain no gates.
  - A gate FAIL ⇒ `CRITICAL_FAIL`.
  - `derived_from_evaluator_output` is `Literal[False]`.
  - Holdout and red-team labelers must be blind to evaluator outputs.
- **RunManifest.**
  - A+ requires A.
  - Private data only in a locked run.
  - A locked run needs a tag, a clean tree, every hash verified, the canonical profile, no blocking pending item and no mock backend.
  - A tuning run records its round.

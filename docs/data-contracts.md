# Data contracts

The frozen documents in [`docs/spec/`](spec/) define the vocabulary. The contracts below implement it as
pydantic v2 models (`extra="forbid"`), versioned in `src/ignosis_eval/versions.py` and exported as JSON Schema
into `schemas/` (`ignosis-eval schemas export`; `tests/test_contracts.py` keeps them in sync).

## Versioning policy

- A contract change bumps its schema string in `versions.py`, re-exports `schemas/` and updates this page.
- The spec-pack versions (`SPEC_CONTRACT_VERSION = 1.2.0-frozen`, `SPEC_RUBRIC_VERSION = 1.2-mvp`,
  `SPEC_PROFILE_ID = collections_default_v1`, `SPEC_PROFILE_VERSION = 1.1.1`) are checked at load time. A mismatch
  fails closed.
- Every enum in `rubric.yaml › enums` is mirrored exactly in `contracts/enums.py`. A drift test enforces it.
  `MeasurementBasis` (`duration | words`) is no longer a rubric enum in 1.2; it mirrors `TRT-06.finding_field`.

| Contract | Schema | Module | Purpose |
|---|---|---|---|
| NormalizedInput | `normalized_input/3.0.0` | `contracts/canonical_input.py` | What a system sees: the random per-run `unit_alias` (`u_` + 8 hex, P-17 / SC-05; its only identifier; no item id, unit id, file name, path, split or pack), input and unit mode, the §2.3 header, the call-level `role_mapping_confidence` (DC-00, AJ-05), 1-based turns (evaluation, supplied and ASR text; reliability; per-turn `diarization_confidence`), content-addressed audio ref (sha256 + format, no file name) and ASR ref, and the front-end result (evaluability, pre-checks, step status) |
| EvaluationRecord | `evaluation_record/3.0.0` | `contracts/evaluation_record.py` | Output of K0 / A / A+ / B: record status, `confidence_source` (SELF_REPORTED for A, COMPUTED otherwise; AJ-06), verdict + critical status + `within_scope_complete`, evaluability (PARTIAL derived, AJ-04), all G1–G9 gate results (reason codes include `NO_AGENT_TURN_AFTER_TRIGGER`, SC-01), findings (TRT-06 carries `measurement_basis: duration | words`, AJ-02), explicit check statuses, outcome, tags, routing tier, the always-OOS list, unverified agent commitments |
| ExtractionOutput | `extraction/2.0.0` | `contracts/extraction.py` | Evaluator B's extraction (LLM call #1) per rubric 1.2 `extraction_schema` (AJ-07): events (`event_id` ^E, `type`, `turn`, `quote`, `source`, `confidence`; borrower `strength`, required on human/stop requests) with typed per-type fields (`responds_to` on the `carried_by` types, `kind`, `items`, `claims_human`, `offer_type`/`accepted_turn`, `value`/`basis_stated`, `category`/`negated`, `route`, single-value `corrects`/`new_value_id`), agent stated values (`value_id` ^V, `raw`, `component_of` → a payable_total), commitments, and the typed `call_frame` (agent_org_statement, identity_checks, non_conversation, stage_hint). JSON Schema in `schemas/extraction.schema.json`; a drift test holds the literals equal to the rubric |
| GoldLabel | `gold_label/2.1.0` | `contracts/gold_label.py` | Content-level gold per item (SD-01). Mode gold is derived by `golddrv/`, never stored. No gold critical status (AJ-12) |
| CaseCard | `case_card/2.2.0` | `contracts/case_card.py` | Author intent (B-01); 2.2.0 adds the SC-01 reason code to its enum |
| ItemMeta | `item_meta/1.1.0` | `contracts/benchmark.py` | `item.json`: split, pack, language, unit modes, artifacts, synthetic flag, `tuning_only` (e.g. G-02-N5: never scored) |
| Registries | `registries/1.1.0` | `contracts/registries.py` | Controls, pairs, twins (SD-01); a pair may declare `incidental_differences` (BD-03, BD-04) that never define its target |
| Blueprint | `gold_blueprint/1.1.0` | `contracts/blueprint.py` | The frozen DEV `dev-gold-blueprint.yaml` (design intent, not gold): items with `rule_basis` and `external_dependencies`; `depends_on` rejected (BD-05) |
| BenchManifest / GoldManifest | `bench_manifest/2.1.0`, `gold_manifest/2.0.0` | `contracts/benchmark.py` | Hash lists of a scope (dev or private) |
| RunManifest / RunCompletion | `run_manifest/3.1.0` | `contracts/run_manifest.py` | §17 run artifacts, lock rules, system configs (P-3; `llm_backend` adds `openai` in 3.1.0); `unit_alias_mapping_sha256` and `p17_payload_strings_checked` (P-17) |
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
- **NormalizedInput.**
  - `unit_alias` matches `^u_[0-9a-f]{8}$`; anything else (an item id, a file name, an old `in-` alias) is a schema
    error. The runner assigns it per unit per run (P-17); `input_sha256` excludes it, so replay fixtures are stable.
  - Before any system is called, the runner applies the P-17 identity test to every unit's input (bench-a1 item ids,
    pair / twin ids, rubric check ids in free text, benchmark labels, the unit's own source names) and fails the run
    on a hit. Ordinary words are not leaks.
  - `AudioRef` carries `sha256` and `format` only; the front end resolves the file privately.
  - The front end fails closed (`NormalizationError`) if the evaluated text contains the item id, the item directory
    name, a non-generic artifact file stem or a pair id.
- **ExtractionOutput.**
  - A type-specific field on the wrong event type, or a missing required one, is a schema error; `strength` on an
    agent event is one, and `strength` is required on `human_request` / `stop_request`.
  - `event_id` / `value_id` follow `^E[0-9]+$` / `^V[0-9]+$` and are unique; `component_of` must name another stated
    value of type `payable_total`; a correction's `corrects` and `new_value_id` must name existing stated values.
  - `engine/extraction.py` drops (and logs) an event whose quote fails SD-13 or whose turn speaker does not match the
    event side, and removes `responds_to` ids that do not name an earlier BORROWER event or sit on a type outside
    `responds_to.carried_by`.
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
  - The P-10 system alias mapping hash and the P-17 unit alias mapping hash are both required.
  - Private data only in a locked run.
  - A locked run needs a tag, a clean tree, every hash verified, the canonical profile, no blocking pending item and no mock backend.
  - A tuning run records its round.

# Data contracts

Every artifact that crosses a boundary (benchmark to evaluator, evaluator to storage, gold to scorer)
is a typed Pydantic v2 model with `extra="forbid"`. Unknown fields are rejected, so drift cannot pass
silently. Each serialized artifact carries an exact `schema_version` literal. Machine-readable JSON
Schemas are exported to `schemas/*.schema.json`, and `tests/test_runner.py` fails if they go stale.

Legend: **[spec]** = named in the task brief. **[prov]** = a provisional choice made here that must be
reconciled with the Stage 1–4 documents (see `gap-analysis.md`).

## Versioning policy

| Contract | Version constant | Current |
|---|---|---|
| Canonical Input | `CANONICAL_INPUT_SCHEMA` | `canonical_input/1.0.0` |
| Evaluation Record | `EVALUATION_RECORD_SCHEMA` | `evaluation_record/1.0.0` |
| Gold Label | `GOLD_LABEL_SCHEMA` | `gold_label/1.0.0` |
| Case Card | `CASE_CARD_SCHEMA` | `case_card/1.0.0` |
| Profile | `PROFILE_SCHEMA` | `profile/1.0.0` |
| Benchmark / Gold manifest | `BENCHMARK_MANIFEST_SCHEMA`, `GOLD_MANIFEST_SCHEMA` | `…/1.0.0` |
| Run manifest | `RUN_MANIFEST_SCHEMA` | `run_manifest/1.0.0` |

A shape change bumps the version, which invalidates every older artifact on validation. That is
intentional: old artifacts must be migrated explicitly, never reinterpreted.

## 1. Canonical Input (`contracts/canonical_input.py`)

This is the only thing an evaluator sees about a call.

| Field | Notes |
|---|---|
| `call_id` [spec] | Opaque. Visible to the evaluator, so it must not encode the scenario (check B010). |
| `input_mode` [spec] | `audio_only` \| `transcript_only` \| `audio_transcript`. |
| `language` | `primary` (BCP-47), `others`, `code_mixed`. |
| `call_start_ts` [spec] | Timezone-aware ISO-8601. Required for contact-hours checks. |
| `transcript` [spec] | `provenance` + `turns`. |
| `transcript.provenance` [spec] | `source` (human_verbatim, human_corrected_asr, vendor_asr, pipeline_asr, synthetic_script), ASR engine/model/version, diarization, timestamp source, `derived_from_audio_sha256`. |
| `turns[]` [spec] | `turn_id`, `index`, `speaker` [spec] (agent/customer/third_party/system/unknown), `text`, `start_ms`/`end_ms` [spec timestamps], `asr_confidence`, `language`. |
| `audio` [spec: audio metadata] | Relative `uri`, `sha256`, format, codec, sample rate, channels, duration, channel map, `rendering` (TTS engine, voices, noise, SNR, codec simulation, seed, renderer version), `contains_real_pii`. |
| `evidence_availability` [spec] | transcript, audio, turn_timestamps, speaker_labels, call_start_ts, call_start_captured, call_end_captured. |
| `evaluability` [spec: evaluability metadata] | Upstream-observable quality signals (`known_issues`, SNR, ASR confidence). **Not** a gold label and **not** a judgement. |

Invariants:
- `transcript_only` needs a transcript, no audio, and no pipeline-ASR transcript.
- `audio_only` needs audio; a transcript may be attached **only** by the pipeline's own ASR during
  normalization.
- `audio_transcript` needs both.
- `evidence_availability` must agree with what is actually present.
- Turns need unique ids, `index == position`, `start ≤ end`, non-decreasing start times, and must not
  extend more than 1 s past the audio duration.
- The audio URI must be relative.

**Normalized input.** `pipeline/normalize.py` turns an `audio_only` input into one with a
`pipeline_asr` transcript after verifying the audio hash. If ASR yields nothing, it appends
`asr_failed`. The normalized input is persisted for every repetition.

**Capabilities.** `contracts/capabilities.py` derives what an input makes observable: content, audio,
turn_timestamps, speaker_labels, call_start_ts, call_start_captured. The profile maps each gate,
defect and dimension to the capabilities it requires. This map drives modality conformance.

## 2. Evaluation Record (`contracts/evaluation_record.py`)

All evaluators (A, A+, B, K0, mocks) emit exactly this.

| Field | Notes |
|---|---|
| `evaluator` [spec: evaluator version] | name, version, architecture (A, A+, B, K0, MOCK), model_id, prompt_hashes, config_hash. |
| `rubric_version`, `profile_id`, `profile_version`, `profile_sha256` [spec] | |
| `input_mode` [spec] | Must equal the normalized input's mode. |
| `evaluability` [spec] | `evaluable` \| `out_of_scope` \| `inconclusive`, plus reasons. |
| `verdict` [spec] [prov] | `pass` \| `fail` \| `inconclusive` \| `out_of_scope`. |
| `gates[]` [spec] | gate_id, status (`pass`/`fail`/`inconclusive`/`not_applicable`), evidence_ids, confidence, rationale. |
| `dimensions[]` [spec] | dimension_id, evaluable, score within scale, evidence_ids. |
| `findings[]` [spec] | finding_id, defect_id, severity, gate_id, dimension_id, repair_status, evidence_ids, **attribution** [spec] (target + evidence), confidence. |
| `evidence[]` [spec] | evidence_id, modality (transcript/audio/metadata), turn_ids, quote, start/end ms, speaker, metadata_field. |
| `confidence` [spec] | overall, method, calibrated. |
| `primary_attribution` [spec: attribution] | Optional record-level cause. |
| `observable_outcomes` [spec] | outcome codes, outcome class (win/loss/neutral/unknown), evidence. |
| `dangerous_win`, `clean_loss` [spec] | bool or null (null on abstentions). |
| `routing` [spec] | auto_accept \| human_review \| compliance_escalation \| no_action, plus reasons. |
| `experiment` [spec: experiment metadata] | run_id, item_id, repetition, seed, rep_seed, timestamps. Attached **by the runner**; evaluators leave it null. |

**Structural validation** (at parse time): types and enums, unique ids, evidence shape per modality,
and dimension score within scale.

**Semantic invariants** (checked by `contracts/record_checks.py` so the scorer can *count* violations;
failing one makes the item-rep an integrity failure):
- verdict ↔ evaluability agree [prov G3];
- **gate precedence**: any gate FAIL ⇒ verdict FAIL [prov G4];
- no dangling evidence references, and no cited turn ids unknown to the input;
- every finding cites evidence;
- gate, defect and dimension ids are known to the profile;
- call_id, input_mode and profile match;
- dangerous_win and clean_loss are not both true.

**Modality violations** (for modality conformance): evidence of a modality the input lacks, or a
PASS/FAIL gate, finding or scored dimension whose required capability is missing.

`EvaluationRecord.content_hash()` hashes everything except the runner-attached `experiment` block.
It is the basis of the reproducibility test.

## 3. Gold Label Record (`contracts/gold_label.py`)

Gold is completely separate from evaluator output.

| Field | Notes |
|---|---|
| `item_id`, `split`, `scenario` [spec] | `item_id` is the benchmark case id. |
| `expected_evaluability`, `expected_verdict`, `acceptable_verdicts` [spec] | |
| `expected_gates[]` [spec: gate status] | status + `acceptable_statuses` (for defensible alternatives). |
| `expected_defects[]` [spec] | defect_id, severity, gate_id, repair_status, `required` (must-detect vs acceptable), **expected evidence** [spec] (required/acceptable turns, audio spans, metadata fields), **expected attribution** [spec] + `attribution_determinable`. |
| `acceptable_extra_defects` | Defensible extra findings; these are not counted as unsupported. |
| `expected_primary_attribution`, `expected_outcome`, `expected_dangerous_win`, `expected_clean_loss` | |
| `confidence` [spec] | high \| moderate \| low. |
| `contested` [spec] | uncontested \| contested_resolved \| contested_unresolved, notes, alternative verdicts. |
| `provenance` [spec] | source (author_specified/independent_label/adjudicated), case-card ref + hash, labeling protocol version, created_at, `derived_from_evaluator_output: false` (literal). |
| `labelers[]` [spec: labeler metadata] | Pseudonymous id, role, time, `blind_to_case_card`, `saw_evaluator_outputs`. |
| `adjudication` [spec] | required, adjudicator, time, method, disagreements, resolution. |

Invariants:
- Gold cannot claim to derive from evaluator output. Extra fields such as a `source_run_id` are
  rejected.
- For the holdout split, no labeller may have seen evaluator outputs.
- Gate precedence and verdict ↔ evaluability hold in gold too.
- Defect ids are unique (repeated instances fold into one defect with multiple evidence turns).
- A defect cannot be both expected and an acceptable extra.
- A label cannot be both dangerous win and clean loss.
- Contested-unresolved labels cannot be HIGH confidence.
- `adjudicated` provenance requires a completed adjudication block.

**Write protection.** The evaluator process cannot read or write gold (audit-hook guard). Frozen gold
files are read-only on disk and hash-verified before and after every run and before scoring.

## 4. Case Card, Profile and manifests

- **Case Card** (`contracts/case_card.py`): see `benchmark-authoring.md`.
- **Profile** (`contracts/profile.py`): gates, dimensions and defects with severities and capability
  requirements, plus the outcome-code → outcome-class map. Its hash is recorded in every run manifest.
  **The shipped profile is a placeholder.**
- **Benchmark manifest**: per-case metadata (derived from the case card) and file hashes (input,
  audio, sidecars, card), a content fingerprint, per-split hashes, and the dataset hash.
- **Gold manifest**: per-item gold hashes, split hashes, gold hash, the benchmark-manifest hash it was
  frozen against, and the labeling protocol version.
- **Holdout registry**: an append-only list of holdout case ids with content fingerprints.
- **Run manifest / completion**: see `experiment-protocol.md`.

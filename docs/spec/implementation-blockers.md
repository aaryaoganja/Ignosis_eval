# Implementation Blockers — PENDING HUMAN INPUT (contract `1.1.0-frozen`)

These items are **intentionally unresolved**. The implementing agent must build interfaces and placeholders for them and must **not** choose values, draft content, or substitute defaults.

**Legend:**
- **Blocks:** what cannot be built or run until the item is resolved.
- **Proceed now:** what can be built without it.

| ID | Item | Decision / input required | Owner | Blocks | Proceed now |
|---|---|---|---|---|---|
| **B-01** | Benchmark case content | Case cards (scenario, target behavior, intended labels, pair membership) and transcripts for every bench-a1 item. Also: which variant each dev micro item (MD-G1..G6, MD-C1..C2) uses and the target gates of MD-C1..C2; which 4 holdout snippets are recorded; AB-07's language and script. **Authoring constraints below.** | Case author (Animesh) | Dev tuning, all runs | Parsers, schemas, fixtures using obviously synthetic stubs |
| **B-02** | Gold labels | Blind labeling by Labeler X (and Y if available), adjudication, contested tier, label freeze (hash). Content-level gold; mode-level gold is derived by code. | Labelers + adjudicator | Scoring on real items | Scorer implementation and fixtures; labeling template, importer, agreement tooling |
| **B-03** | Red-team cases | Six RT items plus intent cards by an independent author, written **after** evaluator freeze, with no access to prompts, dev items or case cards. | Independent author | Red-team run | Nothing RT-specific; the runner is generic |
| **B-04** | Lexicon sign-off | A native Hindi/Hinglish reviewer fills `profile.yaml › lexicons.*.terms` (prohibited consequences, negation, recontact cues, voicemail cues, offer keywords, numerals, dates) and resolves known collisions (`do`, `saath`, `na`). Also: how to detect romanized non-supported languages (DC-LANG). | Native reviewer | K0; lexicon path of G2a/G3 (HIGH ceiling); normalizer accuracy claims; DC-LANG | Lexicon loader, matching engine, negation-window logic; tests with synthetic placeholder terms |
| **B-05** | LLM model snapshot | The exact pinned snapshot ID, API access and key handling, max_tokens, seed support. | Animesh | Any real LLM call (A, B) | LLM client interface; a mock client that replays recorded fixtures |
| **B-06** | ASR / diarization | An Indic/code-mix-capable ASR with word timestamps and confidences; the diarization approach; licence; where it runs; cost per audio minute. Also fixes the ASR-dependent thresholds in `profile.yaml`. | Animesh | Audio modes; `asr_*` thresholds | ASR adapter interface, cache by file hash, timing-signal computation on stub timestamps |
| **B-07** | Weaker ASR for platform-style transcripts | A second, weaker ASR used only to *generate* `A+T(platform)` transcripts for P-01, P-02 and S-02 (hand-crafted errors are forbidden). | Animesh | A+T(platform) runs; the perception-attribution test | Generation script interface |
| **B-08** | Pricing snapshot | Per-token prices (input, output, cached if applicable), currency, date; ASR price per minute. | Animesh | The cost metric (SD-25) | Token usage capture; cost calculator taking a price table |
| **B-09** | Audio policy | Consent from role-play participants; voices (2–3 borrower speakers); the TTS voice for agent lines; channel layout (dual-channel default vs mono items to test diarization); file format and sample rate; noise type for the telephony simulation. SNR levels are frozen as 20/10 dB (degraded) and 5 dB (poor) and need confirmation. Also retention and PII handling, and the method for recording the S-02 overlap. | Animesh | Audio recording; modality pack | Telephony simulation script (8 kHz, μ-law, parameterized noise and SNR) |
| **B-10** | Labeling setup | Who Labeler X and Y are, their availability; the calibration session (CAL-01..03); the adjudicator for contested gate items; fallback to single-labeler gold (which must then be disclosed). | Animesh | B-02 | Templates and tooling |
| **B-11** | Profile sign-off and pending thresholds | Confirm the `FROZEN_FOR_ASSIGNMENT` defaults. Decide `loop_similarity` (method and threshold), `asr_low_confidence_word`, `asr_unreliable_call_share`, `material_span_min_confidence`, `non_conversation_min_borrower_words`, `overlap_min_seconds`, `diarization_turn_min_confidence` (AJ-05), approved consequence wording (optional), and whether a helpline is part of the vulnerability protocol. ASR-dependent thresholds are tuned on dev audio only. | Animesh | Benchmark freeze; affected checks | All logic, reading thresholds from config |
| **B-12** | Tuning timebox | Wall-clock timebox per architecture (proposed 4 hours) in addition to the 3 rounds. | Animesh | Dev tuning | — |
| **B-13** | Primary disposition precedence | Display order when a call has several observable dispositions. Not scored. | Animesh | UI display only | Outcome stored as a set of flags |
| **B-14** | `HIGH_FRICTION` threshold | Minor-finding count that triggers the tag. Does not affect the verdict. | Animesh | Tag only | Minor counting |
| **B-15** | PLT severity escalation | When (if ever) PLT-01/02/03 escalate from MINOR to MAJOR. | Animesh | PLT severity beyond the default | PLT detection at default MINOR |
| **B-16** | Optional real Ignosis calls | Whether 5–10 anonymized real calls can be obtained and used as a separately reported "real" stratum; anonymization rules. | Ignosis / Animesh | Any real-call claims | Nothing |

## Authoring constraints for B-01 and B-03 (frozen)
1. **Not drafted by the implementing agent.** If an LLM assists drafting, it must be a different model family from the evaluator LLM. Every item is hand-edited.
2. **Realism checklist:** fillers, partial sentences and self-corrections, at least one interruption per full call, natural Hinglish, borrower evasiveness, **no rubric vocabulary in borrower speech**.
3. **No verbatim or near-verbatim reuse** in holdout or red-team items of any phrasing quoted in the spec documents or the design conversation (these phrasings are public).
4. **Pairs:** write the base call naturally, then edit only the target behavior (1–3 agent turns). Same split, length within ±10%.
5. **Stage must be stated in-conversation** wherever RES-02 is tested (otherwise it is `STAGE_UNKNOWN`).
6. **A-04 must carry** `truncated_start: true` or an explicit in-text marker.
7. **G7 items must carry** a `call_start_ts` header. Audio-only renderings of such items expect G7 = `OUT_OF_SCOPE`.
8. **Case cards, transcripts and audio are frozen and hash-listed before any evaluator prompt exists.**
9. **G1 scope (AJ-01, FP-14):** K-01, MC-06 and every "no disclosure" item must avoid presupposing a loan relationship before affirmation: no protected item (`loan_existence`, `amount`, `overdue_status`, `loan_details`) before the borrower affirms identity. Naming the organization alone is allowed.
10. **G5 (AJ-03, FP-14):** items meant to test G5 decision-table row 4 or row 7 must be labeled with the decision table.
11. **TRT-06 (AJ-02, FP-14):** monologue items should exceed both 30 s and 80 words (`monologue_max_seconds`, `monologue_max_words`), so they hold in every mode.

## Not blocked — safe to build now
- The contracts package (JSON Schemas generated from the spec files, and loaders)
- Transcript parsers
- The normalized-input builder
- Role assignment (no LLM fallback)
- The deterministic normalizer (with synthetic placeholder lexicon terms in tests)
- Evaluability and pre-checks
- The evidence verifier, rule engine, confidence ceiling, attribution rules, verdict engine and tags, all driven by hand-written extraction JSON fixtures
- The ASR adapter interface with a mock
- Timing signals
- The telephony simulation script
- The scorer, with all SD-31 fixtures
- K0 (engine only; lexicon empty until B-04)
- The runner and harness with a mock LLM, the lock guard and append-only storage
- The independent gold-derivation module
- Labeling templates and agreement tooling

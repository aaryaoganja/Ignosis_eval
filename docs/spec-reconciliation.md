# Spec reconciliation — implementation vs the frozen specification pack

- **Branch:** `spec/frozen-stage4`
- **Base:** `423d2e4` ("Add documentation and result-status warnings"), the infrastructure-phase implementation.
- **Authority:** the frozen Stage-5 package, reconciled verbatim: contract `1.2.0-frozen`, rubric `1.2-mvp`, profile
  `collections_default_v1` `1.1.1` (the six files in [`docs/spec/`](spec/)), and the freeze records in
  [`docs/freeze/`](freeze/) (`FREEZE-HANDOFF.md`, `FREEZE-public.md`, `STAGE5-ADJUDICATION-LOG.md`). Where this
  document and the spec disagree, the spec wins.
- **Provenance:** the 1.2 files are the owner's files, copied byte-for-byte; all 13 hashes of `FREEZE-HANDOFF.md`
  verify (`tests/test_public_dev.py::test_freeze_records_commit_all_thirteen_files`, and PD001 on every
  `bench check`). The earlier 1.1.0 copy had been patched by the implementing agent from the adjudication text; that
  record is superseded and kept only as history in [`docs/history/final-adjudication-1.1.md`](history/final-adjudication-1.1.md).
- **Scope of the change:** make the implementation match the spec without redesigning the architecture. The
  following were *not* done: no benchmark content, transcripts, audio, gold, registry entries or red-team items; no
  prompt authoring or tuning; no official runs; no UI; no invented policy values or lexicon terms;
  `PENDING_HUMAN_SIGNOFF` is preserved everywhere.

## 0. Stage-5 frozen package (1.2.0-frozen)

Contract §0 now carries AJ-01..AJ-12 plus the Stage-5 adjudications SC-01..SC-08, BD-01 and BD-02
(`docs/freeze/STAGE5-ADJUDICATION-LOG.md` is a verbatim extract; `test_spec_consistency.py` checks that every row
appears verbatim in §0). The mapping of each decision to the implementation:

| ID | Decision (short) | Implementation | Tests |
|---|---|---|---|
| SC-01 | Terminal trigger: a non_response check (except G5) with no AGENT turn after its trigger is INCONCLUSIVE, no trigger, `NO_AGENT_TURN_AFTER_TRIGGER` | `ReasonCode.NO_AGENT_TURN_AFTER_TRIGGER`; `engine/rules.py::terminal_trigger` / `terminal_trigger_applies` (scope read from each check's rubric `attribution_class`). B's rule engine (`engine/code_rules.py`) applies it to every non_response check except G5 | `test_rules.py` (SC-01 block), `test_spec_consistency.py::test_sc01_*` |
| SC-02 | Unreliable audio cannot contradict a reliable transcript in AUDIO_TRANSCRIPT | Not executable yet: DC-DIV needs the normalizer and DC-01-audio needs the PENDING ASR thresholds (B-06/B-11). The front end records DC-DIV as `not_implemented` and DC-01-audio as `pending_signoff` in every manifest. Nothing in the code lets audio override a transcript | `test_spec_consistency.py::test_sc02_reliability_precondition_present` |
| SC-03 | Evaluability order: DC-02 before the no-BORROWER ROLE_UNCERTAIN clause | `pipeline/prechecks.py::run_frontend` applies `rubric.yaml › evaluability_order` first-match: DC-00 (mapping < 0.85 or no AGENT turn) → ROLE_UNCERTAIN; else no BORROWER turn → DC-02 NON_CONVERSATIONAL; the no-BORROWER clause is then unreachable, as specified | `test_frontend.py::test_sc03_evaluability_order`, `test_non_conversational_call_still_runs_prechecks` |
| SC-04 | Must-not-fire controls may have gold PASS or NA | `metrics/compute.py::sd08` admits gold ∈ {PASS, NA}; PD016 is now an error only outside {PASS, NA} | `test_metrics.py::test_sd08_control_target_with_gold_na_counts`, `test_public_dev.py::test_control_target*` |
| SC-05 | Opaque unit aliases (P-17) | `contracts/unit_alias.py` (alias, item-ID pattern, pack/split words), `NormalizedInput.unit_alias` (random `u_` + 8 hex; `normalized_input/3.0.0`), `pipeline/normalize.py` (audio renamed to the alias before ASR), `runner/aliases.py` (per-run aliases, private mapping in the blinding dir and `$BENCH_PRIVATE_DIR/run_aliases/`, hash in `RunManifest.unit_alias_mapping_sha256`, pre-run payload test), B017 at authoring time; the guard now protects the whole results root and the whole private root during evaluation | `test_runner.py::test_p17_*`, `test_audio_is_renamed_to_the_alias_before_asr`, `test_evaluator_isolation.py` |
| SC-06 | §12 carries IDs, split, pack and pair IDs only for holdout / red team | The contract is the owner's redacted file; `benchmark/public_dev.py::frozen_dev` parses the 3-column table and PD019 rejects any §12 row with more; the repository's own holdout-intent mentions (intake docstring, case-card template, tests, authoring doc) were removed | `test_public_dev.py::test_section_12_*`, `test_spec_consistency.py::test_sc06_*` |
| SC-07 | Changelog for BD-01 | Contract §0 and §12.2 (verbatim) | `test_spec_consistency.py::test_stage5_log_is_a_verbatim_extract_of_contract_section_0` |
| SC-08 | A non-explicit distress cue makes G6 NA | Rubric `G6.na_when` (verbatim). J-G6 and the G6 rule are next-phase B components; nothing implemented contradicts it | `test_spec_consistency.py::test_sc08_g6_na_when_non_explicit` |
| BD-01 | MC-05 intent redesigned (private) | Nothing in the repository (holdout intent is private) | — |
| BD-02 | A-01 single-purpose; bench-a1 contains no G7 positive; G7 recall not measured | PD020 (design), B019 (gold); `metrics.json` carries `unmeasured.G7_recall = UNMEASURED` and a warning; G7 is covered by deterministic tests (`test_frontend.py::test_g7_calling_window`) | `test_public_dev.py::test_no_g7_positive_in_bench_a1`, `test_g7_positive_is_rejected`, `test_runner.py::test_dev_run_blind_score_reveal` |

Other 1.2 text changes that required code changes:

| Source change | Implementation |
|---|---|
| `extraction_schema` restructured (`event_id` ^E, `source`, borrower-only `strength` required on human/stop requests, `responds_to.carried_by`, `offer.accepted_turn`, single-value `correction`, `value_id` ^V, required `raw`, `component_of` → a payable_total, typed `call_frame`) | `contracts/extraction.py` (`extraction/2.0.0`), `engine/extraction.py` (turn speaker must match the event side), `engine/rules.py` |
| G5 `honoring_event` (human request: a route in {human_transfer, callback, escalation} whose `responds_to` includes the request; stop request: `stop_honored` route responding to it, or the first non-collection agent turn after the request or after the last collection turn); `decision_table` keys `order/when/also_emit`; worst result FAIL > INCONCLUSIVE > PASS | `engine/rules.py::g5_decide`, `evaluators/prompts.py::rubric_section` |
| ACC-05 `acc_05_rules` (components excluded; no later correction targeting the earlier value) and `repair_rules.major_to_minor_when` (correction corrects the conflicting value, before the commitment turn, not contested) | `engine/rules.py::acc05`, `_repair` |
| `outcome_model.commitment_turn` (confirmed turn, else offer acceptance turn, else payment-in-call claim turn) | `engine/rules.py::commitment_turn` (the 1.1 `proposed_turn` fallback is removed) |
| `positive_ptp_requires_firmness` removed (AJ-09 as prose) | `spec/registry.py` reads the firmness from `positive_ptp_rule`, fail closed |
| `measurement_basis` no longer a rubric enum; TRT-06 `finding_field: duration | words` | `MeasurementBasis.WORDS`; `evaluation_record/3.0.0` |
| `architecture_application`: `A` has "no verdict recomputation"; `A_plus` steps with fixed contents (step 2 = OOS codes and `outcome.verified` only; step 4 LOW on role mismatch; step 7 `repair`) | `engine/frontend_merge.py` (A's verdict is no longer raised by a merged pre-check), `engine/finalize.py`, `engine/confidence.py` |
| SD-04 code majority status (PASS when neither emitted nor abstained in ≥ 3 reps); SD-09 pooled per-rep unsupported passes; SD-17 critical-status split over fired gold-FAIL units only | `metrics/majority.py`, `metrics/compute.py` (`scorer/1.2.0`, `scoring-spec@1.2.0-frozen`) |
| Authoring constraints are the frozen 1–8 (the reconstructed 9–11 are gone) | B016 removed; constraint 4 checked on the DEV design (PD011) |
| `bench/public/MANIFEST.json` superseded by `docs/freeze/` | PD001 verifies FREEZE-public.md and FREEZE-HANDOFF.md; `bench public-manifest` removed |
| Blueprint `rule_basis` / `external_dependencies` (replacing `depends_on`) | `BlueprintItem` fields; ids checked against contract §0 and the B-xx blockers; the cards' `Rule basis:` must agree |

## 1. Reconciliation table

"Current implementation" describes `423d2e4`. "Required change" says what the spec requires and what this
commit did. The **Risk** column rates the risk of the old state, or of what is still missing:
H (a result would be wrong or unsafe), M (a metric or process would be incomparable), L (cosmetic or contained).

| Area | Current implementation | Frozen specification | Required change | Risk |
|---|---|---|---|---|
| **Rubric / code definitions** | Placeholder profile `config/profiles/collections_placeholder.yaml`, `status: placeholder`. It defined 5 gates (`G_AGENT_DISCLOSURE`, …, `G_CONTACT_HOURS`), 3 dimensions and 11 `DEF_*` defects, each with a `requires:` capability list. | `rubric.yaml` 1.0-mvp: gates G1–G9, MVP codes UND/ACC/RES/COM/TRT/POL-01, PLT-01..04, always-OOS codes, NOT_EVALUATED codes, named sets, verdict rules, judgments. `profile.yaml`: `collections_default_v1`. | **Done.** `spec/loader.py` loads and version-checks the spec pack (fails closed on mismatch). `spec/registry.py` builds every check definition from `rubric.yaml`, and nothing is hand-maintained. Prompts get a rubric section generated from the spec (`evaluators/prompts.py::rubric_section`, P-2). The placeholder profile is no longer referenced (see §6). | H → resolved |
| **Status enums** | `GateStatus` pass/fail/inconclusive/not_applicable. `Verdict` pass/fail/inconclusive/out_of_scope. `EvaluabilityStatus` evaluable/out_of_scope/inconclusive. No check status, no record status, no critical status. | §5 and `rubric.yaml › enums`: check PASS/DEFECT/NA/INCONCLUSIVE/OUT_OF_SCOPE; gate PASS/FAIL/NA/INCONCLUSIVE/OUT_OF_SCOPE, with CONFIRMED/SUSPECTED on FAIL; verdict CRITICAL_FAIL/NEEDS_ATTENTION/MEETS_BAR/NOT_EVALUABLE plus `within_scope_complete`; record OK/EVALUATION_FAILED; evaluability EVALUABLE/PARTIAL/NOT_EVALUABLE plus 11 reason codes. | **Done.** `contracts/enums.py` mirrors all 24 rubric enums exactly (`RUBRIC_ENUMS`, drift test in `tests/test_spec.py`). Records, gold and cards use them. Unknown values are schema errors (SD-02). | H → resolved |
| **Verdict logic** | `evaluators/builder.py`. OUT_OF_SCOPE evaluability → out_of_scope verdict. A gate FAIL or an unrepaired critical/major agent-side finding → FAIL. Inconclusive → inconclusive. Routing auto_accept/human_review/compliance_escalation, with a 0.7 confidence threshold. | §10 and V0–V7: pre-checks first. NOT_EVALUABLE → NOT_EVALUABLE unless a pre-check gate failed. Any gate FAIL → CRITICAL_FAIL (CONFIRMED iff any failed gate is HIGH). An unrepaired asserted Major → NEEDS_ATTENTION. Otherwise MEETS_BAR. Minors never matter. `within_scope_complete` per V7. Repair rules. Routing tiers 1–6. | **Done.** One deterministic engine, `engine/verdict.py` (`decide`, `apply_repair`, `tags`, `routing_tier`), used by A+, B and K0 via `engine/finalize.py`. V2 sets non-pre-check gates and codes INCONCLUSIVE, never PASS. | H → resolved |
| **Confidence ceiling** (AJ-05, AJ-06) | A numeric self-reported probability on gates and findings, with no ceiling. | §6 (R-01/R-11/R-12): confidence is computed, not asked. HIGH needs a verified quote, reliable spans and either deterministic confirmation or a lexicon hit without negation (G2a/G3: lexicon only). LLM_JUDGE and absence-based codes cap at MEDIUM. The LLM label may only lower it. Routing: FAIL+HIGH → CONFIRMED, else SUSPECTED; INCONCLUSIVE + in-span trigger → SUSPECTED. | **Done.** `engine/confidence.py` (`ceiling`, `compute`, `route_gate`, `finding_state`), with ceilings read from the rubric (the POL-01a/b rule is parsed from `confidence_ceiling_rule`). Confidence is a `HIGH/MEDIUM/LOW` label. A keeps its own labels as the uncorrected baseline (`confidence_source: SELF_REPORTED`, AJ-06); A+, B and K0 recompute them (`COMPUTED`). LOW comes from span unreliability, contradiction, or a citation whose role does not match its turn (A_plus step 4, shared by B); role confidence is call-level (DC-00, AJ-05). | H → resolved |
| **Attribution** | `AttributionTarget` agent_logic/asr/tts/telephony_audio/customer/upstream_data/undetermined. Heuristic assignment; an ASR-attributed defect did not fail the agent. | §7: classes agent_speech / non_response (registration override) / content_perception / timing / tts_render / perception_event / not_attributable. The perception test runs only in AUDIO_TRANSCRIPT + `platform_live_asr`; elsewhere INDETERMINATE (+ `perception_plausible`). Defects are never CUSTOMER_DRIVEN. Basis PROFILE_DEFAULT where applicable. | **Done.** `engine/attribution.py` (evaluator side). The gold side is implemented independently in `golddrv/derive.py::derive_attribution`. Attribution no longer changes the verdict. | H → resolved |
| **Evidence rules** | `EvidenceItem` with string `turn_ids` (0-based `index`), substring check after NFKC/casefold/P+S normalization, ±500 ms timestamp tolerance. | SD-13: NFKC → casefold → P*/S* → space → collapse → strip. Levenshtein window score ≥ `quote_match_min` (90). Reference text by `evidence.source`. Faithful ⇔ turn exists ∧ role matches ∧ score ≥ 90. §9.6: a gate citation that fails is re-judged once, else SUSPECTED + `evidence_unverified`; a non-gate finding with a failing citation is dropped and logged. | **Done.** `contracts/evidence.py` implements SD-13 exactly. `Evidence(turn, quote, role, source, element, header_field)` with **1-based** turns (the SD-12 anchors). The engine verifier and the scorer both use SD-13; the §9.6 handling is in `engine/finalize.py`. | H → resolved |
| **OUT_OF_SCOPE vs INCONCLUSIVE** | `Capability` flags derived from the input. OUT_OF_SCOPE meant "not a call this evaluator is specified for". Missing capability → gate inconclusive. | §5 definitions: NA = does not apply; INCONCLUSIVE = evidence should be carried but is missing, unreliable or contradictory; OUT_OF_SCOPE = the mode does not carry the evidence at all. Always-OOS codes. `oos_reason` MODE_CAPABILITY / EXTERNAL_DATA_REQUIRED. | **Done.** Mode OOS comes from the §8 table (`spec/registry.py::mode_status`; resolved independently in `golddrv/capability.py`, and a test asserts the two agree on every combination). Always-OOS codes are listed with EXTERNAL_DATA_REQUIRED. OUT_OF_SCOPE is never used as a verdict. | H → resolved |
| **Fired definition** | "Detected" = a finding with the same `defect_id` **or** its gate FAIL. No SUSPECTED concept. | SD-03/SD-05: fired ⇔ gate FAIL (either critical status); EVALUATION_FAILED ⇒ not fired. Primary safety metrics use fired (R-04). | **Done.** `metrics/alignment.py::RepObs.fired`, `metrics/majority.py`. | H → resolved |
| **Majority output** (AJ-11) | None. Each item × rep was its own unit; default reps = 1. | SD-04 (1.2.0): every majority quantity is a per-rep boolean indicator true in ≥ 3 of 5 reps; an EF rep sets every gate and code indicator false; gate status FAIL if fired ≥ 3, else the status held ≥ 3, else `NO_MAJORITY` (never correct); code status DEFECT if emitted ≥ 3, else INCONCLUSIVE/OUT_OF_SCOPE/NA held ≥ 3, else PASS if neither emitted nor abstained in ≥ 3, else `NO_MAJORITY`; verdict value held ≥ 3 (EF is a value for the verdict only). No modal status, no tie-breaking. k = 5 (P-5). | **Done.** `metrics/majority.py` (no precedence constants); `NO_MAJORITY` column in SD-06 (reported, never counted as correct or tolerated); `DEFAULT_REPETITIONS = 5`; locked runs refuse k ≠ 5. | H → resolved |
| **Finding matching** | By taxonomy id only. The best finding by turn-id overlap; audio-span overlap; no tolerance. | SD-12: E = union of evidence turns of ASSERTED findings per code; A = gold anchors. Match ⇔ ∃\|t − a\| ≤ 1. A wrong anchor is FP + FN. POSSIBLE excluded. An EF rep ⇒ FN. Pooled P/R; Major and Minor micro-averages; borrower-impact recall; severity mismatch. Gate evidence matching; G7 matches on firing. | **Done.** `metrics/matching.py`, `metrics/compute.py::sd12`. | H → resolved |
| **Abstention scoring** | Abstention precision/recall over item × rep verdict abstentions; `unsupported_pass_verdict` / `_gate`. | SD-09 (unsupported pass = gold INCONCLUSIVE/OOS vs majority PASS/NA; overclaim; unsupported defect; unit- and check-level over-abstention) and SD-10 (targets INCONCLUSIVE/OUT_OF_SCOPE/SUSPECTED/NOT_EVALUABLE; recall; precision without always-OOS codes). | **Done.** `sd09`, `sd10`; gold `abstention_targets`. | M → resolved |
| **Pair inversion** | `pair_inversions` on a symmetric difference of gold defects, or PASS/FAIL order per rep; `collateral_change_rate`. | SD-20: pair registry with `target_check`; majority output; a gate target uses majority fired, a code target uses Match in ≥ 3 reps / emitted in ≤ 2; inversion (H5); collateral = e_diff ⊕ g_diff over gates + Major/Minor codes minus the target. Scoreable holdout pairs MP-02/03/06/07/12, CP-01. | **Done.** `contracts/registries.py` (pairs/controls/twins), `sd20`. Registry **content** is authoring (B-01); `bench/registries.json` ships empty. | M → resolved (content pending) |
| **Consistency** | Verdict, gate and finding-set consistency; mean pairwise agreement / Jaccard. | SD-19: consistent ⇔ same verdict (EF counts as a value) **and** same fired-gate set in all 5 reps; the distribution of the most-frequent-verdict count; the flip-to-pass list. Consistency re-run disabled (R-05). | **Done.** `UnitAgg.consistent`, `sd19`; `SystemConfig.consistency_rerun: Literal[False]`. | M → resolved |
| **A+ derivation** (AJ-06) | A+ made its **own** LLM call (`a_plus_verify` prompt, "verify_findings"), then an evidence check. | §11 / P-4: A+ is derived from A's stored raw output of the same rep. It applies the 8 ordered deterministic steps of `rubric.yaml › architecture_application.A_plus` (capability filter, external-truth filter, evidence verifier, confidence cap, status re-map, attribution, repair, verdict and tags), each with fixed contents in 1.2. No LLM call, no prompt, no tuning. A+ latency = A + derivation; A+ cost = A. A itself gets only schema validation plus the merge of the shared front-end results, with no verdict recomputation (AJ-06). | **Done.** `APlusDeriver.derive` (no client); `engine/finalize.py` logs the steps `1-…`..`8-…` in order (names match the rubric keys); `engine/frontend_merge.py` for A (pre-check results replace A's values; A's verdict and tags stay the LLM's). The runner derives A+ immediately after A and writes `derivation_log.json`. The blinded view composes A+ timing and usage (`runner/blind.py`). `a_plus_verify.md` is unreferenced (§6). | H → resolved |
| **Holdout locking** | `--confirm-holdout`; an `official` flag (clean tree, non-placeholder profile); an append-only holdout registry. No lock, tag or hash-verification record. | P-8 / H7: tag the evaluator commit; manifest `locked: true`; verify the hashes of private data, gold, rubric, profile and lexicons **before** the run_id; the runner refuses `--locked` otherwise; one locked run per frozen evaluator version (§13.3); red team after the holdout (P-9); 24-hour limit (P-6). | **Done.** `runner/lock.py::preflight` + `verify_hashes`; the `RunManifest` validator (locked ⇒ tag + clean + all hashes + canonical profile + no blocking pending item + no mock); `benchmark/holdout.py` (append-only `locked_runs.jsonl`); `--confirm-holdout` kept; 24-hour abandon. Today every locked run is refused (pending sign-offs, B-05). | H → resolved |
| **Gold isolation** | `ProtectedPathGuard` audit hook over `gold/`, `case_cards/`, `manifests/`; gold read-only; re-verification before and after each run; holdout labelers blind. | Invariant: the evaluator never sees gold (CLAUDE.md 1); P-1 (private data outside the repo); P-12 (scorer and gold derivation never import evaluator packages, and vice versa); B-02 blind labeling. | **Kept and extended.** The guard now protects dev and private gold, case cards, manifests, `registries.json` and the blinding directory. Transitive import tests cover both directions. `GoldProvenance.derived_from_evaluator_output` is `Literal[False]`. | H → resolved |
| **Modality capability rules** | `InputMode` audio_only/transcript_only/audio_transcript; audio_only via `MockASR` sidecars; audio never analysed; checked by `check_modality_conformance`. | §2 / §8 / P-5: TRANSCRIPT / AUDIO / AUDIO_TRANSCRIPT; units TRANSCRIPT, T-gold, T-asr, A, A+T, A+T(platform). The capability table (G7 header only, OOS in AUDIO; PLT-01 needs timestamps in TRANSCRIPT; PLT-02 OOS in TRANSCRIPT; PLT-03/04 only with `platform_live_asr`; PERCEPTION only in A+T platform). ASR cached by file hash (P-2). R-08: call time never from audio. | **Done.** `contracts/enums.py::UnitMode`; `pipeline/normalize.py` (per-mode builders); `pipeline/asr.py::ReplayASR` (a cache keyed by audio sha, engine, version and params; a miss raises, B-06). Capability is enforced in the engine and measured by the scorer (H6). | H → resolved (ASR engine pending) |
| **Hard safety requirements H1–H7** | Not implemented. | §14 / SD-27: H1 external-truth (structural + confirmed regex hits), H2 unfaithful gate quotes, H3 every gold-FAIL gate unit detected in ≥ 3/5, H4 zero unsupported passes, H5 zero inversions, H6 zero capability violations, H7 lock audit. Computed separately for the holdout and the red team. | **Done.** Each alias gets `hard_requirements` with PASS / FAIL / PENDING_HUMAN_CONFIRMATION / NOT_APPLICABLE. H1 textual candidates go to `human_checks.csv`. H7 is audited from the view manifest. | H → resolved |
| **Transcript input format** | JSON `CanonicalInput` with string turn ids, 0-based index, speaker agent/customer/third_party/system/unknown, and `evidence_availability` flags. | §2.3 (R-15): `.txt` / `.json` only, the header `call_start_ts` / `transcript_provenance` / `truncated_start`, roles AGENT/BORROWER/OTHER/UNKNOWN (CUSTOMER alias), timestamps all-or-none, reliability markers. | **Done.** `pipeline/intake.py`; `NormalizedInput` (`contracts/canonical_input.py`) with 1-based turns, supplied / ASR text and the front-end result. | H → resolved |
| **Evaluability and pre-checks** (AJ-04, AJ-05) | Heuristic evaluability (no content → inconclusive, no collections keyword → out_of_scope). | §9: reason codes; pre-checks G1c, G7, G8/G9 string parts, POL-01b before evaluability; call-level NOT_EVALUABLE; truncation only from the header or a marker; missing evidence never becomes PASS. | **Partly done.** `pipeline/prechecks.py`, in the SC-03 `evaluability_order`: DC-00 call-level role gate (`role_confidence_min` on the call-level mapping confidence, or no AGENT turn → ROLE_UNCERTAIN; AJ-05), then DC-02 for a call with no BORROWER turn → NON_CONVERSATIONAL, UNKNOWN turns span-unreliable, G7 (calling window, IST), G8/G9 (NA under the default profile), POL-01b, TRANSCRIPT_TRUNCATED from the header. **Pending sign-off:** DC-01-audio, DC-02, DC-03/G1c, DC-LANG (their thresholds and lexicons are PENDING). PARTIAL is derived by the verdict engine per V7 (AJ-04). **Pending sign-off:** the turn-level `diarization_turn_min_confidence` (B-11, AJ-05). **Not implemented:** the in-text truncation marker (no syntax in §2.3), DC-DIV. Every step's status is recorded in the normalized input and the run manifest. | M (pending by design) |
| **LLM protocol** | Mock backend "mock"; a retry policy; `model_id` and temperature recorded. | P-3: one pinned snapshot (no "latest"), temperature 0, seed if supported, transport retries ≤ 3 not counted, 1 schema retry → EVALUATION_FAILED, structured output, consistency re-run off, English free text. | **Done.** `evaluators/llm.py` (`RecordingClient`, `structured_call`), `SystemConfig` rules. The real client still raises (B-05). The mock replays recorded fixtures only. | M (B-05 pending) |
| **K0** | `heuristics.py`: regex lexicons written by the implementer against the test fixtures (e.g. `\bpolice\b`, `besharam`), with a contact-hours window. | §11: the keyword floor uses only the profile lexicon `terms`; deterministic; never tuned; `seed_candidates_unreviewed` are never used (§19). | **Done.** `evaluators/k0.py` uses `profile.lexicons.*.terms` only; all terms are empty until B-04, so K0 fires only the deterministic pre-checks. `heuristics.py` is unreferenced (§6). | H → resolved |
| **Evaluator B** (AJ-03, AJ-07) | Per-gate LLM calls (`b_gate_check`, `b_defect_scan`, `b_outcome`). | §11: extraction (LLM #1) → verifier → rule engine → batched judgments (LLM #2, only if triggered) → attribution → verdict. `rubric.yaml › extraction_schema` (AJ-07). | **Interface done** (`EvaluatorB`, `JudgmentRequest/Answer`, `RuleEngine` ABC). The typed extraction contract follows the 1.2 `extraction_schema` (`contracts/extraction.py`, `extraction/2.0.0`, `schemas/extraction.schema.json`), and so does its evidence verifier (`engine/extraction.py`: the turn speaker must match the event side; invalid `responds_to` ids removed and logged). The adjudicated deterministic rules are implemented and tested as a library (`engine/rules.py`: G1, G2a/G2b, G3 prohibited categories, G4, the G5 decision table with the 1.2 honoring events + RES-06, ACC-03u, ACC-05 + repair, TRT-06, the SC-01 terminal-trigger rule). The full rule engine is B's default: `SpecRuleEngine` (`evaluators/judgement.py`) over `engine/code_rules.py` covers every MVP gate and code, asks the rubric's judgments in one batched call, and integrates the answers (§3.46). The stub prompts carry the generated output contracts (template 0.4.0). Provider: Gemini (§3.48). | Done; no real model call yet (GEMINI_API_KEY absent in this environment); locked-run pin B-05 |
| **Units, ordering, repetitions** | Optional item shuffle; per-rep seeds; one evaluator per run. | P-5 / P-6: unit = (item, mode); k = 5; `order_r = seeded_shuffle(units, BASE_SEED + r)`; `arch_order_r = rotate([A, B], r − 1)`; A+ derived right after A; K0 once before rep 1. | **Done.** `runner/experiment.py` (`seeded_order`, `arch_order`, several systems per run). The ordering algorithm id is recorded in the manifest. | M → resolved |
| **Blind scoring** | None; the scorer read the evaluator name. | P-10: seeded alias mapping SYS-1..n; the mapping hashed into the manifest and stored outside the scorer input path; scorer and human checks use aliases; reveal after the report hash is committed. | **Done.** `runner/blind.py` (mapping at run start, aliased view with its own hash list, reveal gated on the report hash). The scorer reads `blinded/<run_id>/` only. | M → resolved |
| **Storage** | `runs/<run_id>/<item>/rep_<k>/…`, write-once; `scoring/<run_id>/…`. | P-11: `runs/<run_id>/<system>/<item>__<mode>/rep_<k>/{7 files}`, A+ `derivation_log.json`, `scoring/<run_id>/{item_scores, metrics, discordance, human_checks}`; tuning runs `dev-<round>-<timestamp>`. | **Done.** `contracts/run_manifest.py::rep_dir`, `runner/storage.py`. | L → resolved |
| **Data separation / benchmark layout** | `benchmark/{dataset,case_cards,gold}/{dev,holdout,redteam,calibration}` all in the repo; lowercase case ids. | P-1: dev in `bench/dev/`; holdout, red team and their gold in `$BENCH_PRIVATE_DIR`; private files hash-listed in the repo; pairs, twins and renderings never cross splits; canonical bench-a1 ids (R-07). | **Done.** `benchmark/layout.py` (`BenchLayout`, refuses a private dir inside the bench); `integrity/freeze.py` (dev and private hash lists + gold manifests); `bench/` scaffolding (empty). Audio renderings are units of the same item, so they cannot cross splits. The old `benchmark/` tree is unreferenced (§6). | H → resolved |
| **Gold contract and mode-derived gold** | Gold per case with expected defects, required/acceptable turn ids, `attribution_determinable`, and label confidence high/moderate/low. | SD-01: explicit status for every gate (INCONCLUSIVE with trigger Y/N, contested, anchors, label confidence); findings with anchors, post-repair severity, repair, evidence elements, attribution facts; explicit lists; verdict; outcome; tags; abstention targets; implicit PASS/Sure. Mode gold is derived by an independent module (P-12). | **Done.** `contracts/gold_label.py` (gold schema v2); `golddrv/` (capability, attribution, verdict recompute when a mode removes failed gates; undeterminable values left `None`; audio units flagged for the P-12 human spot check). | H → resolved |
| **Case cards** | Card v1 (scenario, category, customer context, target defect…), rules CC/CX/CP/CS. | B-01: cards record intent, target behaviour, intended labels and pair membership; authoring constraints 1–8. | **Done.** Card v2.2 (`contracts/case_card.py`), rules CC001–CC015 (CC015: repair only on ACC-05, AJ-08), CX001–CX004, CP001–CP002; bench checks B001–B019 (B016 removed in 1.2; B017 includes the P-17 payload test; B019: no gold G7 positive, BD-02), including the pair ±10% length warning, the G7 header requirement and the `pii_reviewed` rule. | M → resolved |
| **Intervals** | Wilson by default; Clopper–Pearson for safety metrics; min n 10; no interval for non-independent units. | SD-26: Wilson 95% with z = 1.96 shown only when n ≥ 10; zero-failure bound 1 − 0.05^(1/n) (rule of three only as a label); whole-percent rounding; always k/n. | **Done.** `stats/intervals.py`, `stats/proportion.py`. Pooled metrics carry `clustered: true` as a caveat (§3). Clopper–Pearson removed. | M → resolved |
| **Tags and routing** (AJ-09) | DW = WIN and any critical/major/failed gate; CL = LOSS and none; routing decisions. | §10: positive set; DW-CRITICAL / DW-MATERIAL (inducement-set Major anchored before the commitment turn); Clean Loss; `HIGH_FRICTION` threshold PENDING; coverage `conversation_only`; routing tiers 1–6. | **Done.** `engine/verdict.py::tags`, `routing_tier`; `high_friction` stays `null` (B-14). The positive set counts `PTP_STATED` only with firmness `firm` (AJ-09; one implementation, `spec/registry.py::outcome_positive`). | M → resolved |
| **Scorer outputs and human checks** | `item_scores.csv`, `metrics.json` (29 provisional metrics), `discordance_tables.csv`. | SD-29 / P-13: counts first, a scope line, per-category tables, discordance tables with the exact sign test; human checks for evidence support (rep 1), external-truth hits and contested review. | **Done.** `scoring/scorer.py` writes the four P-11 files + `scoring_manifest.json`; human results are fed back with `--human-checks` into a new `--suffix`. | M → resolved |
| **PENDING_HUMAN_SIGNOFF** | Placeholder values were used directly. | Pending items must not be defaulted; locked runs need the blocking ones resolved. | **Done.** `spec/pending.py` inventories the 18 in-file items (17 + `diarization_turn_min_confidence`, AJ-05) plus the run blockers. `Spec.threshold()` raises `PendingHumanSignoffError`. The manifest records every pending item; locked runs refuse blocking ones. | H → resolved |

## 2. Files changed (summary)

- **New:** `docs/spec/*` (6 spec files, verbatim), `docs/spec-reconciliation.md`, `src/ignosis_eval/spec/`,
  `engine/`, `golddrv/`, `pipeline/{intake,lexicon,prechecks}.py`, `contracts/registries.py`,
  `metrics/{compute,majority,external_truth}.py`, `runner/{blind,lock}.py`, prompt stubs `b_extraction.md` and
  `b_judgments.md`, `bench/` scaffolding (empty; `registries.json` with no entries), new schemas, new tests
  (`test_spec`, `test_intake`, `test_frontend`, `test_engine`, `test_golddrv`, `test_stats`).
- **Rewritten in place:** every contract module; `evaluators/*` (except the old prompt files); `pipeline/{asr,normalize}.py`;
  `benchmark/{layout,checks,case_card_rules,holdout}.py`; `integrity/freeze.py`; `metrics/{alignment,matching,slices,definitions}.py`;
  `scoring/{loader,scorer}.py`; `stats/*`; `runner/{experiment,storage,gitinfo}.py`; `cli.py`; `schemas.py`; `versions.py`;
  the existing tests; README, CLAUDE.md and the `docs/*.md` pages.
- **Untouched:** the existing prompt files (`a_holistic.md`, `a_plus_verify.md`, `b_gate_check.md`,
  `b_defect_scan.md`, `b_outcome.md` are byte-identical); `integrity/guard.py` (logic unchanged).

- **Final adjudication commit (1.1.0):**
  - **Spec:** the six `docs/spec/` files patched per FP-01..FP-14, plus the new `docs/spec/final-adjudication.md`.
  - **New code:**
    - `contracts/extraction.py`;
    - `engine/{rules,extraction,measure,frontend_merge}.py`.
  - **Changed code:**
    - the contracts: canonical input, evaluation record, gold, card, enums;
    - `spec/{loader,registry,pending}.py`;
    - the front end;
    - `engine/{finalize,verdict,confidence}.py`;
    - the evaluators;
    - `metrics/{majority,alignment,compute}.py`;
    - `scoring/scorer.py`;
    - `benchmark/{checks,case_card_rules}.py`;
    - `schemas/`.
  - **Tests:** new `test_rules.py`, `test_extraction.py` and `test_spec_consistency.py`; the existing tests are
    extended.
  - **Untouched:** prompt `.md` files, `bench/` content and every other `bench/` file (except the case-card template's
    schema version and repair comment).

- **DEV benchmark ingestion commit ("ingest frozen dev benchmark"):**
  - **Added:**
    - `bench/public/`: the four Stage 5 DEV design files verbatim, plus `MANIFEST.json` and `README.md`;
    - `.gitattributes`, so git keeps the files byte-exact;
    - `benchmark/public_dev.py` (PD001–PD017);
    - bench checks B017, B018, B040, B041 and B043;
    - CLI `bench public-check` and `bench public-manifest`.
  - **Contract changes:**
    - `ItemMeta.tuning_only` (`item_meta/1.1.0`, `bench_manifest/2.1.0`);
    - `NormalizedInput.input_alias` and `AudioRef` without a file name (`normalized_input/2.2.0`).
  - **Also changed:**
    - the front end fails closed on identifiers in the evaluated text;
    - `bench/public` is guard-protected.
  - **Tests:** `test_public_dev.py`, `test_evaluator_isolation.py`.
  - **Not written:** transcripts, gold, registries entries or case-card YAML.

- **Stage-5 reconciliation commit ("reconcile with frozen stage-5 package 1.2.0"):**
  - **Imported verbatim:** `docs/spec/*` (1.2.0-frozen), `docs/freeze/*` (new), `bench/public/*` (README, cards and
    blueprint replaced; matrix and schema unchanged); `bench/public/MANIFEST.json` removed;
    `docs/spec/final-adjudication.md` moved to `docs/history/final-adjudication-1.1.md` with a superseded banner.
  - **New code:** `contracts/unit_alias.py`, `runner/aliases.py`.
  - **Changed code:** `versions.py`; `spec/registry.py`; the contracts (enums, extraction, canonical input,
    evaluation record, run manifest, case card); `pipeline/{prechecks,normalize,intake}.py`;
    `engine/{rules,extraction,finalize,confidence,frontend_merge,measure}.py`; `evaluators/{base,prompts}.py`;
    `metrics/{majority,compute}.py`; `scoring/scorer.py`; `runner/experiment.py`; `benchmark/{public_dev,checks,layout}.py`;
    `cli.py`; `schemas/`.
  - **Contract versions:** `normalized_input/3.0.0` (`unit_alias` replaces `input_alias`), `evaluation_record/3.0.0`
    (`measurement_basis` value `words`; new reason code), `run_manifest/3.0.0` (unit alias mapping hash, P-17 count),
    `extraction/2.0.0`, `case_card/2.2.0` (new reason code); components `frontend/0.3.0`, `engine/0.3.0`,
    `scorer/1.2.0`, `scoring-spec@1.2.0-frozen`, `rubric-prompt-template/0.3.0`; package `0.4.0`.
  - **Tests:** rewritten `test_rules.py`, `test_extraction.py`, `test_spec_consistency.py`; extended `test_frontend.py`,
    `test_metrics.py`, `test_runner.py`, `test_public_dev.py`, `test_evaluator_isolation.py`, `test_evaluators.py`,
    `test_contracts.py`, `test_spec.py`.
  - **Not written:** transcripts, audio, gold, prompts, registry entries, red-team items; no holdout run.

## 3. Implementation conventions (the spec is silent or ambiguous; each is documented and tested)

1. **Turn numbering is 1-based.** It is the unit of SD-12 anchors and SD-14 elements.
2. **Unit mode spelling:** `A+T(platform)` is stored as `A+T-platform` (directory-safe).
3. **Calling window is half-open, [08:00, 19:00) Asia/Kolkata.** A call starting at exactly 19:00 fails G7.
4. **T-asr** carries the item header (`call_start_ts`, `truncated_start`) with provenance `offline_asr`; its
   text is the cached ASR. **A** carries no header (R-08).
5. ~~PARTIAL is enumerated but no rule produces it.~~ **Superseded by AJ-04:** PARTIAL is derived per V7
   (`engine/verdict.py::derive_evaluability`); the front-end step is `not_applicable`.
6. **K0 gates without a keyword detector are reported PASS** ("nothing detected"). The front end's
   deterministic pre-checks are authoritative.
7. **A+ has no deterministic confirmations** (no extraction), so it reaches HIGH only through the G2a/G3 lexicon
   path or pre-check results. Non-response codes are INDETERMINATE without registration facts. A+ uses the front
   end's evaluability over A's claim. *(Now stated by the spec: AJ-06, `architecture_application.A_plus`.)*
8. **Routing tier is `null`** when no tier rule applies (NOT_EVALUABLE; only REVIEW-type findings).
9. **COM-03 appears in both the Major and the Minor list** (§4.1). Micro-averages bucket each instance by gold
   severity, else by evaluator severity.
10. **A code with only POSSIBLE findings reads INCONCLUSIVE** in the check status (it is excluded from matching by SD-12).
11. ~~SD-04 EF tie-breaking (modal status over OK reps).~~ **Superseded by AJ-11:** no modal status, no
    tie-breaking; ≥ 3-of-5 indicators, EF false, `NO_MAJORITY` otherwise. A 2-2-1 FAIL/PASS/EF split is
    `NO_MAJORITY`, not detected, and not an unsupported pass.
12. **For k ≠ 5 (tuning runs), majority = ⌊k/2⌋ + 1.** It equals 3 for k = 5.
13. **Dev runs apply the U_P definition to dev packs.** Red-team runs use red-team TRANSCRIPT units (SD-27).
14. **Wilson intervals are shown as SD-26 prescribes, even on pooled / clustered units.** Those carry
    `clustered: true` because the nominal coverage is optimistic.
15. **SD-11 free-text fields** are `findings[].description`, `gates[].note` and every attribution `notes` entry.
    `evidence[].quote` is excluded.
16. **H6 counts explicit statuses and findings only.** A code absent from a record is "not emitted" (SD-02) and is
    not a capability claim.
17. **SD-10 precision universe** = gates + MVP codes + PLT codes applicable in the mode (SD-09's definition).
    Always-OOS codes are excluded.
18. **SD-14 `only_for`:** a `dangerous_win` element is required when gold DW ≠ NONE. A sub-rule element (e.g. G1b)
    is required when the labeler recorded it, because gold carries no sub-rule field.
19. **SD-18 DW majority** is the DW value held in ≥ 3 of the k reps (EF reps hold no value), else `NO_MAJORITY`
    (AJ-11; the 1.0.0 "over OK reps" reading is superseded). Mode-derived DW / clean_loss are left undetermined, and
    excluded and counted, when a mode removes the gate they rested on.
20. **The alias mapping is created at run start**, so its hash is in the write-once run manifest. Its secret seed
    is stored in the mapping file.
21. **Reveal** requires the operator to supply the score-report hash. Committing it to git is a human step.
22. **Registries ship empty.** Target checks, control target gates and twin pairs are B-01 content. §12 names pairs
    but, for example, not MP-01's target check.
23. **The gold `label_confidence` vocabulary** is fixed only for `Sure` (SD-01). The rest belongs to B-10.
24. **Transport failure after 3 retries** writes an EVALUATION_FAILED record for that rep. Infrastructure errors
    (replay miss, guard violation, unimplemented component) fail the run closed.
25. **Status-set majority (AJ-11 reading).** A metric defined on a set of statuses (SD-09 {PASS, NA},
    {INCONCLUSIVE, OUT_OF_SCOPE}) uses the per-rep indicator "status ∈ set", true in ≥ 3 reps
    (`UnitAgg.gate_in` / `code_in`).
26. ~~Details the 1.1 adjudication left open (`docs/spec/final-adjudication.md` §3).~~ **Re-assessed against 1.2:**
    - *resolved by 1.2 text:* the `responds_to` carrier list (`carried_by`), the correction fields, the identity-check
      result vocabulary, `confidence_source` values, `measurement_basis` values, the G5 collection-content and
      explicit-strength rules, SD-04 status sets (`passed`, `abstained`);
    - *superseded by 1.2 text:* A's merge raising the verdict on a failed pre-check (1.2: "no verdict
      recomputation"), and the A+ external-truth filter replacing free text with rubric templates and clearing gate
      notes (1.2 step 2 lists only OOS codes and `outcome.verified`). Both are removed;
    - *still conventions (below):* the within-FAIL G5 ranking (37), "not contested" for the ACC-05 repair (38), one
      ACC-05 finding per call (38), the NOT_EVALUABLE short-circuit for A/A+/B (now stated by
      `architecture_application.shared_front_end_all_systems`).
27. **Majority "modal" wording removed from outputs.** SD-19's distribution key is
    `most_frequent_verdict_count_distribution`; per-unit scores carry `reps_holding_most_frequent_verdict`.
28. **Frozen DEV design pack vocabulary.** The design uses the §12 pack names (`core`, `micro`, `modality`,
    `language`). `language` items map to ItemMeta `snippet` (category "Snippet …") or `language_twin`.
29. **"A (<id>@A dev rendering)" is the item's audio rendering**, with the §12.4 unit modes T-gold, T-asr, A and A+T.
    A bare "A" or "A+T" (G-02-N5) is a literal unit mode. A token naming another design item is a cross-reference.
30. **Items whose verdict is "Derived from actual ASR after B-06"** (G-02-N5) are `tuning_only` (ItemMeta
    `scoring_role` = never), matching "never used for scoring claims".
31. **Case-card default gates** ("all default") read G1 PASS, G3 PASS, G7 OUT_OF_SCOPE, all others NA. Any change to
    that sentence fails closed.
32. **Registration fact from the blueprint note.** A non_response finding is registered when its note says "override"
    and not "no registration". This is what the TRANSCRIPT attribution check (§7 via `golddrv.derive_attribution`)
    uses.
33. ~~Opaque input alias `in-` + content hash.~~ **Superseded by SC-05 / P-17:** `NormalizedInput.unit_alias` is a
    random `u_` + 8 hex alias per unit per run. Replay fixtures stay stable because `input_sha256` excludes the alias.
    Audio refs are content-addressed.
34. **SC-03 order is first-match.** `rubric.yaml › evaluability_order` is applied in order and the first call-level
    check that applies gives the NOT_EVALUABLE reason (an unlabeled transcript is ROLE_UNCERTAIN, not also
    NON_CONVERSATIONAL). DC-02's first clause ("no borrower turn with ≥ N words") holds for a call with no BORROWER turn
    whatever the PENDING threshold, so DC-02 is decided for such calls; with BORROWER turns it stays pending (B-11).
    `OTHER` / `UNKNOWN` turns are not BORROWER turns. Test: `test_frontend.py::test_sc03_evaluability_order`.
35. **P-17 pre-run test scope (implementation safety rule; narrowed in the cleanup pass).** Rule 4 targets benchmark
    *identity*, never ordinary vocabulary. It flags canonical bench-a1 item ids built from the P-17 prefixes (any
    case, e.g. `C-08`, `MI-G1-01`, `SN-D01`, `G-02-N5`, `C-04-EN`), pair and twin ids, rubric check ids in free text
    (turns, header values), benchmark labels and aliases (`bench-a1`, `holdout`, `redteam`, `language_twin`,
    `tuning_only`, `SYS-n`, `BD-nn`) and the unit's own item id, directory and file names. Ordinary words such as
    "language", "email", "E-mail", "X-ray", "dev", "core" or "micro" pass. The same test is B017 at authoring time.
    Tests: `test_runner.py::test_p17_*`.
36. **Unit alias mapping storage.** "Stored only in BENCH_PRIVATE_DIR and in the run's private manifest": the mapping
    is written write-once to `<results>/blinding/<run_id>/unit_alias_mapping.json` (guard-protected, outside the
    scorer input) and, when BENCH_PRIVATE_DIR is set, to `$BENCH_PRIVATE_DIR/run_aliases/<run_id>.json`; its hash is in
    the run manifest. The mapping never appears in the manifest itself.
37. **G5 within-FAIL ranking.** 1.2 orders FAIL > INCONCLUSIVE > PASS across triggers; between two FAIL triggers the
    HIGH one is reported (FAIL_HIGH > FAIL_MEDIUM). The stop-request honoring event is the *earlier* of the
    `stop_honored` route and the first non-collection agent turn after max(request, last collection turn).
    Tests: `test_rules.py::test_g5_*`.
38. **ACC-05 details and the commitment-turn safety rule.** "Does not contest" = no `dispute_amount` event after the
    correction and at or before the commitment turn. `commitment_turn` is set only from a confirmed commitment or an
    accepted offer; a payment claim alone never sets it, because the extraction cannot tell an in-call payment claim
    from an already-paid one (it stays null; `commitment_turn_undetermined`). With no commitment of any kind the
    "before the commitment turn" condition holds; when the commitment turn is undetermined, the repair is granted only
    if the correction precedes every payment claim. Rule 1 does not compare an item without any normalized value. One
    ACC-05 finding per call, anchored on the later conflicting statement. Tests: `test_rules.py::test_acc05_*`,
    `test_payment_claim_alone_never_sets_the_commitment_turn`.
39. **Extraction contract details.** The event side comes from the type (the turn speaker must match it). `strength`
    is a borrower field: an agent event carrying it is a schema error. `route` is the route_action field name (the
    rubric's rules read "route in {...}"). A call-frame identity check carries `turn`, `quote` and `result` (G1's
    confirmation is turn order). `agent_org_statement` has no typed shape in the rubric and is opaque JSON. Tests:
    `test_extraction.py`.
40. **Positive PTP firmness** is parsed from the prose `outcome_model.positive_ptp_rule` ("… firmness = firm …") and
    fails closed if the sentence changes shape (`test_spec_consistency.py::test_registry_fails_closed_*`).
41. **Constraint 4 on the DEV design.** The edited set is the card's "Edit only Bx-By" / "Change only Bn"; a
    customer-side beat in it must directly follow an edited agent beat; 1–3 agent beats; beats outside the set must be
    identical. Unlabelled beats (e.g. "close") are counted towards the agent limit. Reported as PD011 warnings unless declared in the pair metadata (BD-03, BD-04).
42. **Canonical evidence-element names (BD-05).** `contracts/evidence_aliases.py` maps the blueprint aliases to the
    rubric names for the validator and SD-14; G5 order-7 `closing_turn` is non-element evidence that marks
    `continued_collection_turns_or_refusal_turn` as having no turn (neither required nor missing gold).
43. **Post-freeze clarifications live in `docs/bd-changelog.md`.** Writing BD-03..BD-05 into `frozen-contract.md`
    §0/§12 was not done (the frozen files stay byte-identical and every freeze hash verifies); the validator reads the
    BD ids from the changelog and accepts them as `rule_basis` / pair-metadata bases.
44. **Transcript QC before the hash freeze** (`benchmark/transcript_qc.py`, `bench transcript-qc`, TQ001–TQ015).
    Deterministic checks only, each with a frozen basis. Constraint 4 on transcripts: turns are aligned by exact
    role + text equality (difflib); the edited agent turns are the larger count over the two members and must be 1–3;
    a changed customer-side turn "directly reacts" when the turn before it is a changed agent turn; the pair's declared
    borrower-context incidentals (BD-03) allow that many other changed customer-side turns. Length ±10% is measured in
    words, as B013. Card word bans are the quoted terms of "must not let the agent say", "Borrower never says" and
    "no rubric words (...)", matched ignoring case. "No words from the prohibited lexicon seeds" uses the profile's
    prohibited-consequence `terms` and `seed_candidates_unreviewed` (authoring QC, not a benchmark run); all-caps seeds
    match their case only (FIR vs the Hinglish "fir"). A surplus header is reported by TQ005 alone. Interruptions
    (realism constraint 2) are not checked: §2.3 defines no notation for them. Tests: `test_transcript_qc.py`.
45. **DEV transcript drafts** (`bench/dev/transcripts/`, owner request 2026-09-29).
    - The drafts live outside `bench/dev/items/` until the freeze, so `bench check` and the manifests do not see
      them yet.
    - G-02-N5 has no authored text. Its card makes it a "script-degraded copy of G-02@A", so its file is G-02's
      turns without the header (the design gives it none). TQ017 enforces the equality.
    - Snippets are one BORROWER turn each (TQ016). The design gives snippets no header, so each snippet's
      `reference_date` (SD-22) is authoring metadata in `provenance.yaml`, not a header.
    - Where a beat sheet ends on an agent read-back of a callback, a one-word borrower confirmation follows
      (MD-G3, MD-C1, MD-C2), so COM-03 does not fire where the design expects no COM-03.
    - In MD-G6 a short agent turn separates the two third-party beats (B2 signal, B3 cue).
    - Tests: `test_transcript_qc.py::test_snippet_shape`, `test_derived_copy`, `test_committed_dev_drafts`.
46. **B's rule engine** (`engine/code_rules.py`, `SpecRuleEngine`). Deterministic rules run first. The rubric's
    LLM_JUDGE checks become one batched judgment request each (J-REG, J-PATH, J-OBJ, J-Q, J-CONS, J-G6, J-FIRM,
    J-CONSTR, J-RIGHTS, J-G8P). A missing, out-of-vocabulary or uncited answer counts as CANNOT_DETERMINE: the check
    is INCONCLUSIVE, and a gate with an in-span trigger is SUSPECTED. The rubric's bare `YES`/`NO` answers are
    restored after YAML 1.1 turns them into booleans.
    Conventions where the rubric leaves the rule open:
    - one J-REG / J-PATH / J-OBJ / J-RIGHTS per code per call, on the first unregistered (or, for J-PATH,
      registered) trigger;
    - ACC-04 when a material question follows an agent-stated value and J-Q = DEFLECTED, else UND-04 (never both);
    - UND-02 and TRT-01 count borrower events whose strength is explicit or unstated;
    - COM-01 treats a raw, un-normalized date or amount as present (the non_specific test needs B-04);
    - COM-03 applies to firm commitments (MAJOR) and to callback routes (MINOR: no later read-back event, or no
      BORROWER turn after it); soft or conditional commitments are COM-02's subject;
    - COM-06 fires only for a route with an explicit null `next_step_quote`; an absent key is INCONCLUSIVE;
    - UND-03 and COM-05 compare digits only (`parse_values`), and anything else is INCONCLUSIVE (B-04);
    - TRT-03 treats `hi-en` as compatible with `hi` and `en`;
    - G6: collection content within the next two agent turns with no care / human / callback route before it;
      DISTRESS_NONEXPLICIT gives NA (SC-08);
    - outcome dispositions map one to one from event types, with `payment_claim` → PAYMENT_CLAIMED_ALREADY_PAID
      (the in-call case cannot be told apart, §3.38); ACKNOWLEDGED or INCOMPLETE when nothing else applies.
    Tests: `test_rule_engine.py`.
47. **DEV draft runs and the intent reference** (`runner/dev_drafts.py`, `devbaseline/`). Drafts run as in-memory
    TRANSCRIPT items with the protocol's front end, P-17 aliases, identity-leak test, guard and write-once storage,
    under `dev_draft_runs/` (not `runs/`). An LLM system of the drafts' assisting family is refused (authoring
    constraint 1), and a missing provider stops the run before anything is written. The baseline's reference is the
    frozen design intent held in memory, never a gold file. The P-17 own-identifier test matches whole tokens, so
    the front end's step name `DC-01-...` is not an item `C-01` leak. Tests: `test_dev_drafts.py`,
    `test_runner.py::test_p17_prerun_check_includes_the_units_own_source_names`.
48. **Evaluator provider: Gemini (owner decision for DEV engineering runs, 2026-09-29).** The owner chose Google
    Gemini as the non-Claude provider. It is family `google-gemini`, so it passes authoring constraint 1 against the
    Claude-assisted drafts. One file holds the provider, the model id and every call setting:
    `evaluators/provider_config.py`.
    - Default model `gemini-3.8-flash`, a stable (GA) model code. `GEMINI_MODEL` overrides it at runtime. A
      `latest` / `preview` / `exp` alias is refused (P-3), and so is anything that is not a Gemini model code. The
      served version (`modelVersion`) is recorded with each response.
    - P-3 settings: temperature 0; seed 0, sent as `generationConfig.seed` and recorded; provider-default
      `maxOutputTokens` (recorded as null); JSON mode (`responseMimeType: application/json`) plus contract
      validation and 1 schema retry; transport retries ≤ 3 with backoff 1 s × 2; timeout 120 s.
    - Temperature risk (low confidence: not checked against the official docs, which this environment could not
      reach): some Gemini-3-generation guidance reportedly recommends keeping the provider's default
      temperature, but P-3 fixes 0. Any degenerate output this causes surfaces as EVALUATION_FAILED, never
      as a PASS.
    - `GeminiClient` calls `models/{model}:generateContent` over stdlib HTTPS and shares `_post_json` (error mapping
      and redaction) with the OpenAI-compatible client.
      - The key is sent only in the `x-goog-api-key` header, never in the URL.
      - HTTP 408 / 409 / 429 / 5xx, timeouts and connection errors are transport errors (retried).
      - Any other HTTP error is a `ProviderRequestError`. On 401 / 403 / 404 or an invalid key it stops a DEV run;
        otherwise that unit's record is EVALUATION_FAILED.
      - A blocked prompt, no candidate or empty text gives empty content, which leads to the schema retry and then
        EVALUATION_FAILED.
      - Thought parts are dropped. Thinking tokens count as output tokens and are logged separately.
    - The key is read only from the runtime environment variable `GEMINI_API_KEY`, by `provider_config.api_key()`.
      It is redacted from every provider error text, and `describe()` exposes only whether it is configured.
      `SystemConfig.llm_backend` adds `gemini` (run_manifest 3.2.0).
    - The spec's B-05 item (the pinned snapshot for locked runs) stays PENDING_HUMAN_SIGNOFF in `docs/spec`, which
      is not edited. This decision covers DEV engineering runs and the review app.
    - Records carry the provider in `system.llm_backend` next to the exact model in `system.model_snapshot_id`
      (evaluation_record 3.1.0). Both are hidden in the blinded view.
      - The version the provider actually served is logged with each response.
      - An unavailable model (HTTP 404) fails with a message naming the model and `GEMINI_MODEL`; no other model
        is ever tried.
      - `ignosis-eval dev smoke` checks the live path on one synthetic app demo call: request, parsing, extraction
        and judgment schemas, record validation, and that no failure becomes a verdict.
    - B lists the agent's `promise_of_action` events as `unverified_agent_commitments` (the EXE-03 `mvp_output`;
      B 0.2.1, engine 0.4.1).
    - The DEV report is labelled "DEV ENGINEERING MEASUREMENT — NOT FINAL RELIABILITY EVIDENCE" and adds Clean
      Loss agreement against the blueprint's `clean_loss` (report 1.1.0).
    Tests: `test_gemini_provider.py`, `test_dev_drafts.py`, `test_rule_engine.py::test_promise_of_action_listed_as_unverified_commitment`,
    `test_secrets.py`.
49. **Review app (clickable MVP; owner request 2026-09-29)** (`app/`).
    - **Host.** The app hosts Evaluator B, as the runner does, inside ProtectedPathGuard. B sees a random unit
      alias. The app never imports gold, card, freeze, scorer, runner or design-validation modules
      (`test_architecture_boundaries.py`). Its reliability screen reads only the committed DEV baseline report.
    - **Intake.** `pipeline/normalize.normalize_supplied` builds the same turns and front end as
      `build_normalized_input`, for a supplied transcript that is not a benchmark item.
      - A+T fingerprints the audio in memory (sha256 + format) and discards the bytes; audio-derived checks stay
        OUT_OF_SCOPE or INCONCLUSIVE.
      - Audio-only was refused with a pathway (ASR / diarization is B-06) until the owner authorized the
        EXPERIMENTAL audio path (§3.50).
    - **Result sources, always labelled.**
      - LIVE EVALUATION: Gemini. When the front end short-circuits a NOT_EVALUABLE call, the deterministic record
        is live without any model call, and the page says no model was called.
      - DEMO / REPLAY: the five synthetic app demo calls, whose scripted extraction and judgment answers are
        replayed through the real rule engine and finalize.
      - EVALUATOR UNAVAILABLE: no key. The front end runs, but there is no verdict.
    - **Demo calls.** Their scripted output is written to be faithful to each demo transcript. They are not
      benchmark content, gold or a measurement, and an edited demo transcript is never replayed.
    - **Library.** In memory, capped at 200 entries, and reset on restart.
    - **Secrets.** The browser talks only to the backend, and no response carries the key.
    - **Product UX pass (owner request 2026-09-29).** Presentation only; no change to B, the rules, contracts or
      metrics.
      - Journey stepper (choose → evaluate → review → explore), a dismissible *How it works* guide, demo calls first
        on the Evaluate screen, and the result framed as step 3 of the same journey.
      - The result follows verdict → why → findings → evidence → uncertainty → outcome → attribution → action.
        Dangerous Win and Clean Loss are explained in place. A record that is not OK (no key, not run, failed)
        shows what ran and never a verdict or a findings section.
      - The reliability screen separates the development measurement from the pending final validation.
      - A fictional sample recording for `demo-settlement` (espeak-ng synthetic voices, `scripts/make_demo_audio.py`,
        sha256 pinned in `demo_calls.json`). The app recognises it by sha256 and links it on the result for playback.
        The bytes are still never evaluated as audio: A+T evaluates the transcript, and audio-only stays refused (B-06).
    Tests: `test_app.py` (incl. `test_sample_recording_is_fictional_synthetic_and_linked`), `test_secrets.py`.
50. **EXPERIMENTAL audio-only evaluation in the review app (owner authorization 2026-09-29: "pragmatic prototype",
    not a validated audio QA system)** (`app/audio_gemini.py`, `pipeline/normalize.normalize_audio_result`).
    - **Two models, two jobs** (`evaluators/provider_config.py`, no fallback for either). `GEMINI_TRANSCRIBE_MODEL`
      (`gemini-3.5-transcribe`) is the ASR adapter: `generateContent` with the recording inline (no Files API, no
      file name, P-17) and `audioTranscriptionConfig {diarization, wordTimestamp}` (verbatim, the default mode).
      `GEMINI_MODEL` (`gemini-3.8-flash`) runs Evaluator B on the resulting turns, unchanged. The transcription model
      never judges; the evaluator never hears audio. A Live (streaming) transcription model is refused.
    - **It is not B-06.** No ASR or diarizer is chosen for the benchmark; the B-11 audio thresholds stay PENDING and
      the DC-01-audio / diarization steps stay `pending_signoff`. Calibration against them has not happened.
    - **Speaker roles: the R-02 outbound-call heuristic, defined here (spec-interpretation choice).** R-02 forbids an
      LLM role fallback and names "diarization plus outbound-call heuristic" without defining the heuristic. Here:
      with exactly two diarized speakers, the speaker who opens the call and also speaks the most words is the AGENT
      and the other the BORROWER. No speaker labels, one speaker, three or more (Google marks 3+ speaker attribution
      experimental), or the two criteria disagreeing (e.g. the borrower answers "Hello?" first) → no role is
      assigned, every turn is UNKNOWN, mapping value 0.0, and DC-00 makes the call NOT EVALUABLE (ROLE_UNCERTAIN).
      Turns that carry no speaker label are UNKNOWN and span-unreliable (AJ-05). The mapping value is 1.0 / 0.0 by
      rule, never a measured confidence, and none is shown.
    - **Timing.** Turn start / end come from the model's word timestamps when every turn has them (then PLT-01 and the
      TRT-06 duration basis use them); otherwise the unit has no timestamps.
    - **Response format.** The exact `generateContent` field names for diarized words could not be confirmed from the
      container (Google's pages are not reachable there). The parser accepts word entries anywhere in the response
      (`word_info`-style annotations or word lists; snake_case or camelCase speaker / offset keys; `1.2s`, number or
      `{seconds, nanos}` offsets) and `spk_N:` line prefixes. With no speaker labels the call is NOT EVALUABLE
      (NO_DIARIZATION) and only the response's key names are logged. Not verified against the live API.
    - **Evidence verification (regression).** The transcription is recorded as both `asr_text` and `supplied_text`:
      it is the only text the evaluator sees, so an extracted quote verifies against the same words whichever
      source tag B's extraction gives it. Without this the verifier dropped every "supplied"-tagged event in an AUDIO
      unit. The verifier and the benchmark A-unit path are unchanged (a TRANSCRIPT unit still rejects "asr" tags).
    - **Labelling.** Every audio-only result, including failed and unavailable ones, carries "EXPERIMENTAL AUDIO
      EVALUATION", "Transcription: <model> · Evaluator: <model>", "Audio transcription and speaker attribution are
      not included in final reliability claims." and "Audio reliability: Not independently calibrated". No key →
      no verdict; the sample recording replays its demo script (labelled DEMO / REPLAY as well).
    - **Isolation.** The module is app-only; no run, benchmark, gold, scorer, metric or evaluator module imports the
      app (`test_experimental_audio_path_stays_in_the_review_app`).
    - **Fail closed (regression).** Model output citing a turn the call does not have (which the rule engine does not
      screen) ends as EVALUATION_FAILED at the app boundary, never a 500 and never a verdict; the engine is unchanged.
    Tests: `test_app.py` (request shape, response shapes, role rule, uncertain / unlabelled speakers, model errors
    incl. 404 and Live ids, the two regressions, labelling, key never exposed), `test_architecture_boundaries.py`.

## 4. Open questions / inconsistencies found in the spec (for the spec owner)

1. ~~SD-04 vs SD-06/SD-07 (modal FAIL without majority fired).~~ **Resolved by AJ-11** (≥ 3-of-5 indicators;
   `NO_MAJORITY`). The `fail_modal_without_majority_fired` count is removed.
2. ~~SD-17 "critical-status mismatch" needs a gold critical status.~~ **Resolved by AJ-12:** the metric is deleted;
   the CONFIRMED/SUSPECTED split is reported descriptively (`sd17.critical_status_split`); overclaim stays.
3. ~~§5 lists `PARTIAL`, but no rule defines it.~~ **Resolved by AJ-04** (V7).
4. **§9 rule 4 accepts an "explicit in-text marker" for truncation, but §2.3 defines no marker syntax.**
5. **§4.3 says COM-04 is "NA while `max_days_out` is null"** but lists it under NOT_EVALUATED ("emit nothing").
   The implementation follows `rubric.yaml › not_evaluated_in_mvp`: emitting COM-04 is a schema error.
6. **SD-10 precision does not say whether mode-OOS PLT codes count.** They are trivially correct in TRANSCRIPT
   mode (convention 17).
7. **Routing (§10) has no tier** for NOT_EVALUABLE or for REVIEW-only findings.
8. **The in-scope pair list (§12.8) has no target checks.** Registry content is needed from B-01.
9. **Calling-window endpoints are not adjudicated** (AJ-10 froze the window, not its boundary). The implementation
   keeps the half-open [08:00, 19:00) IST (convention 3); a call at exactly 19:00:00 fails G7. Owner to confirm.
10. ~~The owner-patched 1.1.0 files were not available.~~ **Resolved:** the frozen 1.2.0 package is the owner's
    files, verbatim and hash-verified (header).
11. ~~SD-08 vs the DEV controls K-01 and K-07 (gold `NA` targets).~~ **Resolved by SC-04:** SD-08 admits gold `PASS`
    or `NA`; the two PD016 warnings are gone, and both blueprint items cite `rule_basis: [SC-04]`.
12. ~~PD014 — evidence element names.~~ **Resolved by BD-05** (canonical mapping, §3.42). Rubric 1.2 keeps the G5 elements `request_turn` and
    `continued_collection_turns_or_refusal_turn` and the POL-01 element `window_or_statement_turn`. The DEV design uses
    `continued_collection_turns` (C-10), `closing_turn` (MD-G5; decision-table order 7 — no honoring event, ≤ 1
    collection turn, no refusal — has no rubric element) and `window_end_turn` (MD-G1). SD-14 completeness keys on
    rubric names. This does not block transcript authoring; the owner must map the names before gold is labeled.
13. ~~Third-party speakers vs DC-00.~~ **Settled by 1.2 text as an authoring rule:** `extraction_schema.event_common.turn`
    says "the speaker of the turn must match the event side (borrower/agent)", `third_party_signal` and
    `vulnerability_cue` are borrower events, and SC-03 makes a call without a BORROWER turn `NON_CONVERSATIONAL`. So
    K-01, C-01 and MD-G6 must label the customer-side speaker `BORROWER`. PD015 stays as a reminder (6 warnings).
14. ~~PD011 — MP-01 / MP-10 header asymmetry.~~ **Resolved by BD-04** (incidental metadata; pair metadata `incidental_differences`). G-02 and K-07 carry a `call_start_ts` header, M-01 and
    C-08 do not, so each pair also differs on G7 (PASS vs OUT_OF_SCOPE). Constraint 4 covers turns; the frozen
    contract neither accepts nor rejects a header difference between pair members.
15. **Blueprint fields left for labelers.** Per the blueprint schema, the labeler still fills in:
    - `verdict.within_scope_complete`;
    - explicit lists;
    - per-gate `contested` / `anchor_turns`;
    - per-finding `repair_status` / `label_confidence` / `attribution_facts`;
    - the evaluability reason.
    The blueprint does not carry these fields; beats become turn ids only after transcripts are frozen.
16. ~~PD011 — M-01 B4 vs authoring constraint 4.~~ **Resolved by BD-03** (incidental borrower context; pair metadata). M-01's card edits B4–B9 of G-02. B4 changes the
    borrower's salary date (7th → 10th) and follows B3, which is not edited; constraint 4 lets only borrower turns that
    directly react to an edited agent turn change. Either the owner accepts B4 as part of MP-01's target behavior
    (COM-05 needs the conflicting constraint), or the design or the constraint needs a BD-xx entry.
17. ~~PD018 — blueprint schema vs items.~~ **Resolved by BD-05** and the `gold_blueprint/1.1.0` contract. `gold-blueprint-schema.yaml` `per_item_fields.meta` still names
    `depends_on (spec clarifications / blockers)`; every item carries `rule_basis` and `external_dependencies`
    instead. Both files are hash-frozen, so the validator uses the item fields and warns.
18. ~~`commitment_turn` third fallback.~~ **Settled by the safety rule** (§3.38): null, never guessed. `outcome_model.commitment_turn` ends with "else payment-in-call claim
    turn", but the 1.2 extraction schema has no field that tells an in-call payment claim from an already-paid one, so
    B cannot derive it from extraction. Affects the ACC-05 repair window and DW-MATERIAL only when there is neither a
    confirmed commitment nor an accepted offer. Convention 38 covers the no-commitment case.
19. ~~P-17 rule 4 scope.~~ **Settled** (§3.35): identity tokens only. "Any evaluator-bound payload" cannot include the rubric/prompt text (it contains
    "language"), and taken literally the rule rejects ordinary words in transcripts. Convention 35 states the scope
    used; the owner should confirm it (or narrow the word list) before transcripts are written.
20. **SC-02 is not yet executable.** DC-DIV and DC-01-audio need the normalizer and the PENDING ASR thresholds
    (B-06, B-11). The reliability precondition is recorded; no code lets audio contradict a transcript.
21. **Private bundle commitment.** `FREEZE-public.md` commits the private registry as
    `c7752ac2…93b1`. Only the holder of `BENCH_PRIVATE_DIR` can verify it; A-01's corrected intent (BD-02) is private
    and is not checked by the repository.
22. **Git history.** Commits before this one on `spec/frozen-stage4` (4bdc9d7..f211cd9) contain the 1.1 §12 with
    one-line holdout and red-team intents. SC-06 governs the current repository contents; rewriting published
    history is destructive and was not done. The owner decides whether the branch history must be purged before the
    repository is shared.

## 5. Not implemented in this commit (next phase; explicit markers in code)

- **B build:** done (§3.46). What B still cannot decide is reported INCONCLUSIVE: RES-11 (B-11), UND-12 (no ask slot
  in extraction 2.0.0), UND-03 / COM-05 on number words (B-04). The evaluator provider is pending (B-05).
- **Deterministic normalizer** (amounts, dates, modes, polarity), and what depends on it:
  - SD-22 snippets;
  - ASR entity error rate;
  - DC-DIV (A+T divergence, §2.4);
  - timing signals for PLT-01/02.
- **Front-end checks awaiting sign-off:** DC-01-audio (and with it SC-02), DC-02 for calls that have BORROWER turns
  (a call without one is decided, SC-03), DC-03/G1c, DC-LANG.
- **Tooling not yet built:**
  - telephony simulation script (B-09);
  - weaker-ASR generation interface (B-07);
  - cost calculator from a price table (B-08; token usage is captured);
  - labeling templates and agreement tooling (B-10).
- **Process steps left to a human:**
  - the P-15 decision procedure (after the reveal);
  - tuning-round logs (P-7).

## 6. Superseded files kept on disk (deletion was not performed)

Stage-5 note: `bench/public/MANIFEST.json` was removed with `git rm` (superseded by `docs/freeze/`), and
`docs/spec/final-adjudication.md` was moved to `docs/history/final-adjudication-1.1.md` (superseded banner), so that
`docs/spec/` holds exactly the six hash-frozen files. The files below are unchanged.

Deleting them was attempted (`git rm`) and **blocked by the session's permission policy** as an irreversible local
destruction. They were therefore left in place, unreferenced by any live module. A test enforces that no live
module imports them or refers to them. **They need the owner's decision:**

- `src/ignosis_eval/evaluators/heuristics.py`: the old regex heuristics; invented lexicon; must never be used (§19).
  Excluded from mypy.
- `config/profiles/collections_placeholder.yaml`: the placeholder taxonomy, replaced by `docs/spec/profile.yaml`.
- `benchmark/` (empty scaffolding and an empty-dataset manifest), replaced by `bench/`.
- `tests/fixtures/benchmark_smoke/` and `tests/fixtures/make_smoke_fixture.py`: realistic-looking example dialogs.
  They conflict with P-1 rule 3 (tests must use minimal, obviously synthetic stubs) and are no longer used by any test.
- `schemas/benchmark_manifest.schema.json`, `schemas/canonical_input.schema.json`,
  `schemas/holdout_registry.schema.json`: schemas of retired contracts.
- `src/ignosis_eval/evaluators/prompts/{a_plus_verify,b_gate_check,b_defect_scan,b_outcome}.md`: prompts of the
  retired designs (A+ now has no prompt; B uses `b_extraction` / `b_judgments`). Left byte-identical.

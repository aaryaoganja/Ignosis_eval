# Spec reconciliation — implementation vs the frozen specification pack

- **Branch:** `spec/frozen-stage4`
- **Base:** `423d2e4` ("Add documentation and result-status warnings"), the infrastructure-phase implementation.
- **Authority:** the six files in [`docs/spec/`](spec/): contract `1.1.0-frozen`, rubric `1.1-mvp`, profile
  `collections_default_v1` `1.1.0`, after the final Stage 4 adjudication (AJ-01..AJ-12, FP-01..FP-14; see
  [`docs/spec/final-adjudication.md`](spec/final-adjudication.md)). Where this document and the spec disagree, the
  spec wins.
- **Provenance of 1.1.0:** the owner's patched spec files were not available to the implementing agent. The pack was
  patched from the adjudication text, and the details the text leaves open are listed in `final-adjudication.md`
  §3. The owner must diff their files against `docs/spec/`; a difference is a bug in this copy.
- **Scope of the change:** make the implementation match the spec without redesigning the architecture. The
  following were *not* done: no benchmark content, gold, red-team items or realistic transcripts; no prompt tuning;
  no official runs; no UI; no invented policy values or lexicon terms; `PENDING_HUMAN_SIGNOFF` is preserved
  everywhere.

## 0. Final adjudication (1.1.0-frozen)

The second commit on this branch ("finalize stage 4 specification adjudication") applies AJ-01..AJ-12. Rows and
conventions below that the adjudication changed are marked **(AJ-xx)**; superseded 1.0.0 conventions are struck
through in §3, and resolved open questions are marked in §4. The per-decision record (rule, spec location, code,
tests) is `docs/spec/final-adjudication.md`. `tests/test_spec_consistency.py` checks that the six files and the code
carry one interpretation of G1, G5, PARTIAL, confidence, repairability, the PTP outcome, majority output,
critical-status scoring and the calling window.

## 1. Reconciliation table

"Current implementation" describes `423d2e4`. "Required change" says what the spec requires and what this
commit did. The **Risk** column rates the risk of the old state, or of what is still missing:
H (a result would be wrong or unsafe), M (a metric or process would be incomparable), L (cosmetic or contained).

| Area | Current implementation | Frozen specification | Required change | Risk |
|---|---|---|---|---|
| **Rubric / code definitions** | Placeholder profile `config/profiles/collections_placeholder.yaml`, `status: placeholder`. It defined 5 gates (`G_AGENT_DISCLOSURE`, …, `G_CONTACT_HOURS`), 3 dimensions and 11 `DEF_*` defects, each with a `requires:` capability list. | `rubric.yaml` 1.0-mvp: gates G1–G9, MVP codes UND/ACC/RES/COM/TRT/POL-01, PLT-01..04, always-OOS codes, NOT_EVALUATED codes, named sets, verdict rules, judgments. `profile.yaml`: `collections_default_v1`. | **Done.** `spec/loader.py` loads and version-checks the spec pack (fails closed on mismatch). `spec/registry.py` builds every check definition from `rubric.yaml`, and nothing is hand-maintained. Prompts get a rubric section generated from the spec (`evaluators/prompts.py::rubric_section`, P-2). The placeholder profile is no longer referenced (see §6). | H → resolved |
| **Status enums** | `GateStatus` pass/fail/inconclusive/not_applicable. `Verdict` pass/fail/inconclusive/out_of_scope. `EvaluabilityStatus` evaluable/out_of_scope/inconclusive. No check status, no record status, no critical status. | §5 and `rubric.yaml › enums`: check PASS/DEFECT/NA/INCONCLUSIVE/OUT_OF_SCOPE; gate PASS/FAIL/NA/INCONCLUSIVE/OUT_OF_SCOPE, with CONFIRMED/SUSPECTED on FAIL; verdict CRITICAL_FAIL/NEEDS_ATTENTION/MEETS_BAR/NOT_EVALUABLE plus `within_scope_complete`; record OK/EVALUATION_FAILED; evaluability EVALUABLE/PARTIAL/NOT_EVALUABLE plus 11 reason codes. | **Done.** `contracts/enums.py` mirrors all 24 rubric enums exactly (`RUBRIC_ENUMS`, drift test in `tests/test_spec.py`). Records, gold and cards use them. Unknown values are schema errors (SD-02). | H → resolved |
| **Verdict logic** | `evaluators/builder.py`. OUT_OF_SCOPE evaluability → out_of_scope verdict. A gate FAIL or an unrepaired critical/major agent-side finding → FAIL. Inconclusive → inconclusive. Routing auto_accept/human_review/compliance_escalation, with a 0.7 confidence threshold. | §10 and V0–V7: pre-checks first. NOT_EVALUABLE → NOT_EVALUABLE unless a pre-check gate failed. Any gate FAIL → CRITICAL_FAIL (CONFIRMED iff any failed gate is HIGH). An unrepaired asserted Major → NEEDS_ATTENTION. Otherwise MEETS_BAR. Minors never matter. `within_scope_complete` per V7. Repair rules. Routing tiers 1–6. | **Done.** One deterministic engine, `engine/verdict.py` (`decide`, `apply_repair`, `tags`, `routing_tier`), used by A+, B and K0 via `engine/finalize.py`. V2 sets non-pre-check gates and codes INCONCLUSIVE, never PASS. | H → resolved |
| **Confidence ceiling** (AJ-05, AJ-06) | A numeric self-reported probability on gates and findings, with no ceiling. | §6 (R-01/R-11/R-12): confidence is computed, not asked. HIGH needs a verified quote, reliable spans and either deterministic confirmation or a lexicon hit without negation (G2a/G3: lexicon only). LLM_JUDGE and absence-based codes cap at MEDIUM. The LLM label may only lower it. Routing: FAIL+HIGH → CONFIRMED, else SUSPECTED; INCONCLUSIVE + in-span trigger → SUSPECTED. | **Done.** `engine/confidence.py` (`ceiling`, `compute`, `route_gate`, `finding_state`), with ceilings read from the rubric (the POL-01a/b rule is parsed from `confidence_ceiling_rule`). Confidence is a `HIGH/MEDIUM/LOW` label. A keeps its own labels as the uncorrected baseline (`confidence_source: SELF_REPORTED`, AJ-06); A+, B and K0 recompute them (`COMPUTED`). LOW comes from span unreliability or contradiction only; role confidence is call-level (DC-00, AJ-05). | H → resolved |
| **Attribution** | `AttributionTarget` agent_logic/asr/tts/telephony_audio/customer/upstream_data/undetermined. Heuristic assignment; an ASR-attributed defect did not fail the agent. | §7: classes agent_speech / non_response (registration override) / content_perception / timing / tts_render / perception_event / not_attributable. The perception test runs only in AUDIO_TRANSCRIPT + `platform_live_asr`; elsewhere INDETERMINATE (+ `perception_plausible`). Defects are never CUSTOMER_DRIVEN. Basis PROFILE_DEFAULT where applicable. | **Done.** `engine/attribution.py` (evaluator side). The gold side is implemented independently in `golddrv/derive.py::derive_attribution`. Attribution no longer changes the verdict. | H → resolved |
| **Evidence rules** | `EvidenceItem` with string `turn_ids` (0-based `index`), substring check after NFKC/casefold/P+S normalization, ±500 ms timestamp tolerance. | SD-13: NFKC → casefold → P*/S* → space → collapse → strip. Levenshtein window score ≥ `quote_match_min` (90). Reference text by `evidence.source`. Faithful ⇔ turn exists ∧ role matches ∧ score ≥ 90. §9.6: a gate citation that fails is re-judged once, else SUSPECTED + `evidence_unverified`; a non-gate finding with a failing citation is dropped and logged. | **Done.** `contracts/evidence.py` implements SD-13 exactly. `Evidence(turn, quote, role, source, element, header_field)` with **1-based** turns (the SD-12 anchors). The engine verifier and the scorer both use SD-13; the §9.6 handling is in `engine/finalize.py`. | H → resolved |
| **OUT_OF_SCOPE vs INCONCLUSIVE** | `Capability` flags derived from the input. OUT_OF_SCOPE meant "not a call this evaluator is specified for". Missing capability → gate inconclusive. | §5 definitions: NA = does not apply; INCONCLUSIVE = evidence should be carried but is missing, unreliable or contradictory; OUT_OF_SCOPE = the mode does not carry the evidence at all. Always-OOS codes. `oos_reason` MODE_CAPABILITY / EXTERNAL_DATA_REQUIRED. | **Done.** Mode OOS comes from the §8 table (`spec/registry.py::mode_status`; resolved independently in `golddrv/capability.py`, and a test asserts the two agree on every combination). Always-OOS codes are listed with EXTERNAL_DATA_REQUIRED. OUT_OF_SCOPE is never used as a verdict. | H → resolved |
| **Fired definition** | "Detected" = a finding with the same `defect_id` **or** its gate FAIL. No SUSPECTED concept. | SD-03/SD-05: fired ⇔ gate FAIL (either critical status); EVALUATION_FAILED ⇒ not fired. Primary safety metrics use fired (R-04). | **Done.** `metrics/alignment.py::RepObs.fired`, `metrics/majority.py`. | H → resolved |
| **Majority output** (AJ-11) | None. Each item × rep was its own unit; default reps = 1. | SD-04 (1.1.0): every majority quantity is a per-rep boolean indicator true in ≥ 3 of 5 reps; an EF rep sets every gate and code indicator false; gate status FAIL if fired ≥ 3, else the status held ≥ 3, else `NO_MAJORITY` (never correct); verdict value held ≥ 3 (EF is a value for the verdict only). No modal status, no tie-breaking. k = 5 (P-5). | **Done.** `metrics/majority.py` (rewritten; no precedence constants); `NO_MAJORITY` column in SD-06; `DEFAULT_REPETITIONS = 5`; locked runs refuse k ≠ 5. | H → resolved |
| **Finding matching** | By taxonomy id only. The best finding by turn-id overlap; audio-span overlap; no tolerance. | SD-12: E = union of evidence turns of ASSERTED findings per code; A = gold anchors. Match ⇔ ∃\|t − a\| ≤ 1. A wrong anchor is FP + FN. POSSIBLE excluded. An EF rep ⇒ FN. Pooled P/R; Major and Minor micro-averages; borrower-impact recall; severity mismatch. Gate evidence matching; G7 matches on firing. | **Done.** `metrics/matching.py`, `metrics/compute.py::sd12`. | H → resolved |
| **Abstention scoring** | Abstention precision/recall over item × rep verdict abstentions; `unsupported_pass_verdict` / `_gate`. | SD-09 (unsupported pass = gold INCONCLUSIVE/OOS vs majority PASS/NA; overclaim; unsupported defect; unit- and check-level over-abstention) and SD-10 (targets INCONCLUSIVE/OUT_OF_SCOPE/SUSPECTED/NOT_EVALUABLE; recall; precision without always-OOS codes). | **Done.** `sd09`, `sd10`; gold `abstention_targets`. | M → resolved |
| **Pair inversion** | `pair_inversions` on a symmetric difference of gold defects, or PASS/FAIL order per rep; `collateral_change_rate`. | SD-20: pair registry with `target_check`; majority output; a gate target uses majority fired, a code target uses Match in ≥ 3 reps / emitted in ≤ 2; inversion (H5); collateral = e_diff ⊕ g_diff over gates + Major/Minor codes minus the target. Scoreable holdout pairs MP-02/03/06/07/12, CP-01. | **Done.** `contracts/registries.py` (pairs/controls/twins), `sd20`. Registry **content** is authoring (B-01); `bench/registries.json` ships empty. | M → resolved (content pending) |
| **Consistency** | Verdict, gate and finding-set consistency; mean pairwise agreement / Jaccard. | SD-19: consistent ⇔ same verdict (EF counts as a value) **and** same fired-gate set in all 5 reps; the distribution of the most-frequent-verdict count; the flip-to-pass list. Consistency re-run disabled (R-05). | **Done.** `UnitAgg.consistent`, `sd19`; `SystemConfig.consistency_rerun: Literal[False]`. | M → resolved |
| **A+ derivation** (AJ-06) | A+ made its **own** LLM call (`a_plus_verify` prompt, "verify_findings"), then an evidence check. | §11 / P-4: A+ is derived from A's stored raw output of the same rep. It applies 8 ordered deterministic steps (capability filter, external-truth filter, evidence verifier, confidence cap, status re-map, attribution, repair allowlist, verdict and tags; `rubric.yaml › architecture_application`). No LLM call, no prompt, no tuning. A+ latency = A + derivation; A+ cost = A. A itself gets only schema validation plus the front-end merge (AJ-06). | **Done.** `APlusDeriver.derive` (no client); `engine/finalize.py` logs the steps `1-…`..`8-…` in order; `engine/frontend_merge.py` for A. The runner derives A+ immediately after A and writes `derivation_log.json`. The blinded view composes A+ timing and usage (`runner/blind.py`). `a_plus_verify.md` is unreferenced (§6). | H → resolved |
| **Holdout locking** | `--confirm-holdout`; an `official` flag (clean tree, non-placeholder profile); an append-only holdout registry. No lock, tag or hash-verification record. | P-8 / H7: tag the evaluator commit; manifest `locked: true`; verify the hashes of private data, gold, rubric, profile and lexicons **before** the run_id; the runner refuses `--locked` otherwise; one locked run per frozen evaluator version (§13.3); red team after the holdout (P-9); 24-hour limit (P-6). | **Done.** `runner/lock.py::preflight` + `verify_hashes`; the `RunManifest` validator (locked ⇒ tag + clean + all hashes + canonical profile + no blocking pending item + no mock); `benchmark/holdout.py` (append-only `locked_runs.jsonl`); `--confirm-holdout` kept; 24-hour abandon. Today every locked run is refused (pending sign-offs, B-05). | H → resolved |
| **Gold isolation** | `ProtectedPathGuard` audit hook over `gold/`, `case_cards/`, `manifests/`; gold read-only; re-verification before and after each run; holdout labelers blind. | Invariant: the evaluator never sees gold (CLAUDE.md 1); P-1 (private data outside the repo); P-12 (scorer and gold derivation never import evaluator packages, and vice versa); B-02 blind labeling. | **Kept and extended.** The guard now protects dev and private gold, case cards, manifests, `registries.json` and the blinding directory. Transitive import tests cover both directions. `GoldProvenance.derived_from_evaluator_output` is `Literal[False]`. | H → resolved |
| **Modality capability rules** | `InputMode` audio_only/transcript_only/audio_transcript; audio_only via `MockASR` sidecars; audio never analysed; checked by `check_modality_conformance`. | §2 / §8 / P-5: TRANSCRIPT / AUDIO / AUDIO_TRANSCRIPT; units TRANSCRIPT, T-gold, T-asr, A, A+T, A+T(platform). The capability table (G7 header only, OOS in AUDIO; PLT-01 needs timestamps in TRANSCRIPT; PLT-02 OOS in TRANSCRIPT; PLT-03/04 only with `platform_live_asr`; PERCEPTION only in A+T platform). ASR cached by file hash (P-2). R-08: call time never from audio. | **Done.** `contracts/enums.py::UnitMode`; `pipeline/normalize.py` (per-mode builders); `pipeline/asr.py::ReplayASR` (a cache keyed by audio sha, engine, version and params; a miss raises, B-06). Capability is enforced in the engine and measured by the scorer (H6). | H → resolved (ASR engine pending) |
| **Hard safety requirements H1–H7** | Not implemented. | §14 / SD-27: H1 external-truth (structural + confirmed regex hits), H2 unfaithful gate quotes, H3 every gold-FAIL gate unit detected in ≥ 3/5, H4 zero unsupported passes, H5 zero inversions, H6 zero capability violations, H7 lock audit. Computed separately for the holdout and the red team. | **Done.** Each alias gets `hard_requirements` with PASS / FAIL / PENDING_HUMAN_CONFIRMATION / NOT_APPLICABLE. H1 textual candidates go to `human_checks.csv`. H7 is audited from the view manifest. | H → resolved |
| **Transcript input format** | JSON `CanonicalInput` with string turn ids, 0-based index, speaker agent/customer/third_party/system/unknown, and `evidence_availability` flags. | §2.3 (R-15): `.txt` / `.json` only, the header `call_start_ts` / `transcript_provenance` / `truncated_start`, roles AGENT/BORROWER/OTHER/UNKNOWN (CUSTOMER alias), timestamps all-or-none, reliability markers. | **Done.** `pipeline/intake.py`; `NormalizedInput` (`contracts/canonical_input.py`) with 1-based turns, supplied / ASR text and the front-end result. | H → resolved |
| **Evaluability and pre-checks** (AJ-04, AJ-05) | Heuristic evaluability (no content → inconclusive, no collections keyword → out_of_scope). | §9: reason codes; pre-checks G1c, G7, G8/G9 string parts, POL-01b before evaluability; call-level NOT_EVALUABLE; truncation only from the header or a marker; missing evidence never becomes PASS. | **Partly done.** `pipeline/prechecks.py`: DC-00 call-level role gate (`role_confidence_min` on the call-level mapping confidence, plus an AGENT and a BORROWER turn; AJ-05), UNKNOWN turns span-unreliable, G7 (calling window, IST), G8/G9 (NA under the default profile), POL-01b, TRANSCRIPT_TRUNCATED from the header. **Pending sign-off:** DC-01-audio, DC-02, DC-03/G1c, DC-LANG (their thresholds and lexicons are PENDING). PARTIAL is derived by the verdict engine per V7 (AJ-04). **Pending sign-off:** the turn-level `diarization_turn_min_confidence` (B-11, AJ-05). **Not implemented:** the in-text truncation marker (no syntax in §2.3), DC-DIV. Every step's status is recorded in the normalized input and the run manifest. | M (pending by design) |
| **LLM protocol** | Mock backend "mock"; a retry policy; `model_id` and temperature recorded. | P-3: one pinned snapshot (no "latest"), temperature 0, seed if supported, transport retries ≤ 3 not counted, 1 schema retry → EVALUATION_FAILED, structured output, consistency re-run off, English free text. | **Done.** `evaluators/llm.py` (`RecordingClient`, `structured_call`), `SystemConfig` rules. The real client still raises (B-05). The mock replays recorded fixtures only. | M (B-05 pending) |
| **K0** | `heuristics.py`: regex lexicons written by the implementer against the test fixtures (e.g. `\bpolice\b`, `besharam`), with a contact-hours window. | §11: the keyword floor uses only the profile lexicon `terms`; deterministic; never tuned; `seed_candidates_unreviewed` are never used (§19). | **Done.** `evaluators/k0.py` uses `profile.lexicons.*.terms` only; all terms are empty until B-04, so K0 fires only the deterministic pre-checks. `heuristics.py` is unreferenced (§6). | H → resolved |
| **Evaluator B** (AJ-03, AJ-07) | Per-gate LLM calls (`b_gate_check`, `b_defect_scan`, `b_outcome`). | §11: extraction (LLM #1) → verifier → rule engine → batched judgments (LLM #2, only if triggered) → attribution → verdict. `rubric.yaml › extraction_schema` (AJ-07). | **Interface done** (`EvaluatorB`, `JudgmentRequest/Answer`, `RuleEngine` ABC). The typed extraction contract is done (`contracts/extraction.py`, `schemas/extraction.schema.json`), and so is its evidence verifier (`engine/extraction.py`, invalid `responds_to` ids dropped and logged). The adjudicated deterministic rules are implemented and tested as a library (`engine/rules.py`: G1, G2a/G2b, G3 prohibited categories, G4, the G5 decision table + RES-06, ACC-03u, ACC-05 + repair, TRT-06). The full rule engine (the remaining codes, judgments, wiring as B's default) is the next build phase: `NotImplementedRuleEngine` is still B's default and fails explicitly. New stub prompts `b_extraction.md` and `b_judgments.md` (unoptimized). | M (next phase) |
| **Units, ordering, repetitions** | Optional item shuffle; per-rep seeds; one evaluator per run. | P-5 / P-6: unit = (item, mode); k = 5; `order_r = seeded_shuffle(units, BASE_SEED + r)`; `arch_order_r = rotate([A, B], r − 1)`; A+ derived right after A; K0 once before rep 1. | **Done.** `runner/experiment.py` (`seeded_order`, `arch_order`, several systems per run). The ordering algorithm id is recorded in the manifest. | M → resolved |
| **Blind scoring** | None; the scorer read the evaluator name. | P-10: seeded alias mapping SYS-1..n; the mapping hashed into the manifest and stored outside the scorer input path; scorer and human checks use aliases; reveal after the report hash is committed. | **Done.** `runner/blind.py` (mapping at run start, aliased view with its own hash list, reveal gated on the report hash). The scorer reads `blinded/<run_id>/` only. | M → resolved |
| **Storage** | `runs/<run_id>/<item>/rep_<k>/…`, write-once; `scoring/<run_id>/…`. | P-11: `runs/<run_id>/<system>/<item>__<mode>/rep_<k>/{7 files}`, A+ `derivation_log.json`, `scoring/<run_id>/{item_scores, metrics, discordance, human_checks}`; tuning runs `dev-<round>-<timestamp>`. | **Done.** `contracts/run_manifest.py::rep_dir`, `runner/storage.py`. | L → resolved |
| **Data separation / benchmark layout** | `benchmark/{dataset,case_cards,gold}/{dev,holdout,redteam,calibration}` all in the repo; lowercase case ids. | P-1: dev in `bench/dev/`; holdout, red team and their gold in `$BENCH_PRIVATE_DIR`; private files hash-listed in the repo; pairs, twins and renderings never cross splits; canonical bench-a1 ids (R-07). | **Done.** `benchmark/layout.py` (`BenchLayout`, refuses a private dir inside the bench); `integrity/freeze.py` (dev and private hash lists + gold manifests); `bench/` scaffolding (empty). Audio renderings are units of the same item, so they cannot cross splits. The old `benchmark/` tree is unreferenced (§6). | H → resolved |
| **Gold contract and mode-derived gold** | Gold per case with expected defects, required/acceptable turn ids, `attribution_determinable`, and label confidence high/moderate/low. | SD-01: explicit status for every gate (INCONCLUSIVE with trigger Y/N, contested, anchors, label confidence); findings with anchors, post-repair severity, repair, evidence elements, attribution facts; explicit lists; verdict; outcome; tags; abstention targets; implicit PASS/Sure. Mode gold is derived by an independent module (P-12). | **Done.** `contracts/gold_label.py` (gold schema v2); `golddrv/` (capability, attribution, verdict recompute when a mode removes failed gates; undeterminable values left `None`; audio units flagged for the P-12 human spot check). | H → resolved |
| **Case cards** | Card v1 (scenario, category, customer context, target defect…), rules CC/CX/CP/CS. | B-01: cards record intent, target behaviour, intended labels and pair membership; authoring constraints 1–8. | **Done.** Card v2.1 (`contracts/case_card.py`), rules CC001–CC015 (CC015: repair only on ACC-05, AJ-08), CX001–CX004, CP001–CP002; bench checks B001–B016 (B016: TRT-06 monologue length, FP-14), including the pair ±10% length warning, the G7 header requirement and the `pii_reviewed` rule. | M → resolved |
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
   end's evaluability over A's claim. *(Now stated by the spec: AJ-06, `architecture_application.A_PLUS`.)*
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
26. **Details the adjudication leaves open** (the `responds_to` allowed-on list, correction field names, the G5
    worst-result ranking, "not contested" for the ACC-05 repair, A's merge raising the verdict on a failed pre-check,
    the A+ external-truth filter contents, the NOT_EVALUABLE short-circuit for A/A+/B, one ACC-05 finding per call):
    see `docs/spec/final-adjudication.md` §3. Each is pinned by a test.
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
33. **Opaque input alias.** `NormalizedInput.input_alias` = `in-` + 16 hex of the content hash. It is deterministic,
    so replay fixtures stay stable, and it carries no benchmark metadata. Audio refs are content-addressed.

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
10. **The owner-patched 1.1.0 files were not available** (see the header). The reconstructed details in
    `final-adjudication.md` §3 need the owner's diff.
11. **SD-08 vs the DEV controls K-01 and K-07.** Both controls have gold `NA` for their target gate: G1 has no
    disclosure event, and G4 has no offer event. SD-08 counts only gold `PASS` targets, so these two must-not-fire
    targets are outside the targeted-false-fire metric. Either SD-08 should admit `NA`, or the design should label
    them differently (PD016).
12. **G5 row-7 evidence.** The rubric's G5 elements (`request_turn`, `continued_collection_turns_or_refusal_turn`)
    cannot express row 7 (not honored, no collection, no refusal): MD-G5 uses `closing_turn`. C-10 and MD-G1 also
    use non-rubric element names (PD014). SD-14 completeness keys on rubric names.
13. **Third-party speakers vs DC-00 (AJ-05).** In wrong-party calls (K-01, C-01, MD-G6) every customer-side turn is a
    third party. If it is labeled `OTHER`, the call has no BORROWER turn and becomes NOT_EVALUABLE. The rubric's
    `borrower` role for `third_party_signal_turn` implies labeling by channel side (`BORROWER`). This is recorded as
    an authoring rule (PD015, B018).
14. **MP-01 / MP-10 header asymmetry.** G-02 and K-07 carry a `call_start_ts` header but M-01 and C-08 do not, so
    each pair also differs on G7 (PD011).
15. **Blueprint fields left for labelers.** Per the blueprint schema, the labeler still fills in:
    - `verdict.within_scope_complete`;
    - explicit lists;
    - per-gate `contested` / `anchor_turns`;
    - per-finding `repair_status` / `label_confidence` / `attribution_facts`;
    - the evaluability reason.
    The blueprint does not carry these fields; beats become turn ids only after transcripts are frozen.

## 5. Not implemented in this commit (next phase; explicit markers in code)

- **B build:**
  - B rule engine and judgment integration: `NotImplementedRuleEngine`.
  - Wiring `engine/rules.py` into a full rule engine for the remaining codes, plus judgments (the AJ-07 event
    schemas and the adjudicated rules are done).
- **Deterministic normalizer** (amounts, dates, modes, polarity), and what depends on it:
  - SD-22 snippets;
  - ASR entity error rate;
  - DC-DIV (A+T divergence, §2.4);
  - timing signals for PLT-01/02.
- **Front-end checks awaiting sign-off:** DC-01-audio, DC-02, DC-03/G1c, DC-LANG.
- **Tooling not yet built:**
  - telephony simulation script (B-09);
  - weaker-ASR generation interface (B-07);
  - cost calculator from a price table (B-08; token usage is captured);
  - labeling templates and agreement tooling (B-10).
- **Process steps left to a human:**
  - the P-15 decision procedure (after the reveal);
  - tuning-round logs (P-7).

## 6. Superseded files kept on disk (deletion was not performed)

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

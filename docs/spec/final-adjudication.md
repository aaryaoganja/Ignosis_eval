# Final Stage 4 Adjudication — AJ-01..AJ-12, FP-01..FP-14 (contract `1.1.0-frozen`)

| Field | Value |
|---|---|
| Source | Final Stage 4 specification adjudication by the product/spec owner (authoritative) |
| Versions after the patch | contract `1.1.0-frozen`, rubric `1.1-mvp`, profile `collections_default_v1` `1.1.0` |
| Applied | 2026-09-28, branch `spec/frozen-stage4` |
| Supersedes | The 1.0.0-frozen reading of G1, TRT-06, G5, PARTIAL, role confidence, A/A+ post-processing, the extraction fields, repair, the PTP outcome, the `REFERENCE` status, majority output and critical-status scoring |

> **Provenance warning — owner diff required.** The adjudication says the patch list "is already applied to the
> files attached" and that the implementation's copy must be diffed against them. **Those patched files were not
> available to the implementing agent** (not in the repository, not on any remote branch). The six files in
> `docs/spec/` were therefore patched by the implementing agent from the adjudication text. Where the text did not
> fix a detail, the choice is listed in [§3](#3-details-not-fixed-by-the-adjudication-reconstructed-owner-to-confirm)
> and marked RECONSTRUCTED. The owner should diff their patched files against `docs/spec/`; any difference is a bug
> in this copy, and the owner's files win.

This file records each decision as implemented: the rule, where the spec states it, where the code enforces it and
which tests pin it. The rule text is the adjudication's, condensed. The contract's §0 table carries a one-line row per
AJ.

## 1. Decisions AJ-01..AJ-12

### AJ-01 — G1 covers loan existence
- **Rule.**
  - The protected items are `loan_existence`, `amount`, `overdue_status` and `loan_details`.
  - Loan existence means stating or presupposing that the person has or had a loan, EMI, dues, account or outstanding
    balance with the lender.
  - These are not disclosures: naming the calling organization, asking for the borrower by name, or stating a purpose
    that does not reveal a financial relationship. The organization-name exclusion keeps G1 compatible with POL-01a.
- **Spec.**
  - Contract §9a.
  - `rubric.yaml`: `G1.description`, `G1.protected_items`, `extraction_schema.account_disclosure` (items,
    definitions, `not_a_disclosure`).
  - `profile.yaml`: `identity_verification.must_precede`.
  - Blockers: authoring constraint 9.
- **Code.**
  - `contracts/extraction.py` (`DisclosureItem`).
  - `engine/rules.py::g1`: G1a, G1b; unclear reply → SUSPECTED; truncated start → INCONCLUSIVE with no trigger.
- **Tests.** `test_rules.py` (`test_g1_*`, one case per protected item, loan existence alone) and
  `test_spec_consistency.py::test_g1_protected_scope_single_list`.

### AJ-02 — TRT-06: duration if the turn has timestamps, otherwise word count, in every mode
- **Rule.**
  - AUDIO and AUDIO_TRANSCRIPT use duration.
  - TRANSCRIPT uses duration only when timestamped, otherwise words.
  - The finding records `measurement_basis`.
- **Spec.**
  - Contract §8 (capability row and note).
  - `rubric.yaml`: `TRT-06.measurement_rule` (replaces `mode_basis`) and `enums.measurement_basis`.
- **Code.**
  - `engine/measure.py::trt06_measure` and `engine/rules.py::trt06`.
  - `engine/finalize.py` step 3 re-measures cited turns.
  - `contracts/evaluation_record.py`: `Finding.measurement_basis`, on TRT-06 only.
  - `benchmark/checks.py`: B016.
- **Tests.**
  - `test_rules.py::test_trt06_measurement_basis`: 80/81 words; duration wins over the word count.
  - `test_spec_consistency.py::test_trt06_measurement_rule_single_definition`.

### AJ-03 — G5 ordered decision table
- **Terms.** N = agent turns after the request; ct = collection turns before the request is honored (all of them if
  it is never honored).
- **Decision table** (first matching row wins; across triggers the worst result wins, FAIL/HIGH > FAIL/MEDIUM >
  INCONCLUSIVE > PASS):

  | Row | Condition | Result |
  |---|---|---|
  | 1 | Agent refuses the request | FAIL (HIGH if ct ≥ 2, else MEDIUM) |
  | 2 | ct ≥ 2 | FAIL, HIGH |
  | 3 | N = 0, stop request | PASS |
  | 4 | N = 0, human request | INCONCLUSIVE, no trigger, `NO_AGENT_TURN_AFTER_REQUEST` |
  | 5 | Honored, ct = 0 | PASS |
  | 6 | Honored, ct = 1 | PASS + RES-06 |
  | 7 | Not honored, N ≥ 1 | FAIL, MEDIUM → SUSPECTED |

- **What counts as honored.**
  - A stop request is honored by a `stop_honored` route, or by the first agent turn with no collection content.
  - A human request is honored only by a `human_transfer`, `callback` or `escalation` route.
- **Spec.**
  - `rubric.yaml`: `G5.definitions`, `G5.decision_table`, `G5.worst_result_wins`; `medium_when` deleted.
  - RES-06 description = row 6.
  - `enums.reason_code` gains `NO_AGENT_TURN_AFTER_REQUEST`.
  - Blockers: authoring constraint 10.
- **Code.**
  - `engine/rules.py`: `g5_decide` (one trigger → row) and `g5` (worst wins, RES-06 per row-6 request, trace of
    rows).
  - `contracts/enums.py::ReasonCode`.
- **Tests.**
  - `test_rules.py`: `test_g5_row1_*` … `test_g5_row7_*`, first-match and worst-wins.
  - `test_spec_consistency.py`: `test_g5_*`.

### AJ-04 — PARTIAL is a derived display status
- **Rule.**
  - PARTIAL: the call is not NOT_EVALUABLE and at least one in-scope check ends INCONCLUSIVE.
  - Not counted: OUT_OF_SCOPE checks, gates reported SUSPECTED from an in-span trigger, and not-evaluated codes.
  - Otherwise EVALUABLE.
  - `within_scope_complete = EVALUABLE ∧ no POSSIBLE`.
  - No verdict effect.
- **Spec.** Contract §5 and `rubric.yaml` V7.
- **Code.**
  - `engine/verdict.py`: `derive_evaluability` and `decide`, which returns `evaluability`.
  - `engine/finalize.py` step 8.
  - The front end records `EVALUABILITY-PARTIAL` as `not_applicable`.
- **Tests.**
  - `test_engine.py`: `test_partial_derivation`, `test_partial_has_no_verdict_effect_and_wsc`,
    `test_finalize_derives_partial`.
  - `test_spec_consistency.py::test_partial_single_definition`.

### AJ-05 — Role confidence is call-level only (one threshold, 0.85)
- **Rule.**
  - DC-00 checks the call-level speaker/channel → role mapping confidence. The call is also NOT_EVALUABLE with no
    agent turn or no borrower turn.
  - Turn-level speaker doubt is span unreliability, not role confidence. This covers UNKNOWN-labeled turns and turns
    below the PENDING `diarization_turn_min_confidence` (B-11).
  - The unreachable "role confidence below threshold → LOW" rule is deleted.
- **Spec.**
  - Contract §6 and §9.
  - `rubric.yaml`: DC-00, DC-01 `rule_turn_role`, `confidence_rules.low_when_any`.
  - `profile.yaml`: `thresholds.role_confidence_min` note; `thresholds.diarization_turn_min_confidence` = PENDING.
  - Blockers: B-11.
- **Code.**
  - `contracts/canonical_input.py`: `NormalizedInput.role_mapping_confidence`, `Turn.diarization_confidence`.
  - `pipeline/prechecks.py::run_frontend` (DC-00).
  - `pipeline/normalize.py`: UNKNOWN → unreliable.
  - `pipeline/asr.py`: the cache must carry `mapping_confidence`.
  - `engine/confidence.py`: no role-confidence parameter.
  - `spec/pending.py`: the new pending value, which blocks locked runs.
- **Tests.**
  - `test_frontend.py`: `test_dc00_*`, `test_unknown_turns_*`, `test_unknown_turn_evidence_is_low_confidence`,
    `test_diarized_turn_threshold_is_pending`, `test_asr_cache_requires_mapping_confidence`.
  - `test_engine.py::test_low_comes_from_span_unreliability_not_role_confidence`.
  - `test_spec.py` (pending inventory).

### AJ-06 — A is the uncorrected baseline; A+ is deterministic post-processing; B unchanged
- **A.**
  - The LLM emits gate statuses, critical status, findings, confidence, attribution, repair flags, outcome, tags and
    verdict, with `confidence_source: SELF_REPORTED`.
  - The only post-processing is schema validation plus the front-end merge: the not-evaluable short-circuit and the
    pre-checks G7, G8 (exact-string part), G9 and POL-01b.
  - No capability filter and no external-truth filter: A must be able to violate H1 and H6.
- **A+.**
  - Zero LLM calls.
  - Eight ordered steps: capability filter → external-truth filter → evidence verifier → confidence cap → status
    re-map → attribution → repair allowlist → verdict and tags.
  - The confidence cap reaches HIGH only via the G2a/G3 lexicon path or pre-check results; everything else is capped
    at MEDIUM.
  - No registration override.
- **B.** Full rubric ceilings and the `responds_to` override.
- **Spec.**
  - Contract R-01, §6 and §11.
  - `rubric.yaml`: `architecture_application` and `enums.confidence_source`.
  - Protocol P-4.
- **Code.**
  - `contracts/evaluation_record.py`: `confidence_source`, required on OK records.
  - `engine/frontend_merge.py` (A's merge and short-circuit).
  - `engine/finalize.py` (logged steps `1-…` to `8-…`).
  - `evaluators/pipelines.py`: `EvaluatorA`, `APlusDeriver`, `EvaluatorB`.
- **Tests.** `test_evaluators.py`:
  - `test_a_keeps_violations_a_plus_removes_them`;
  - `test_a_plus_steps_follow_rubric_order`;
  - `test_a_short_circuits_not_evaluable_without_llm_call`;
  - `test_a_merge_applies_precheck_failures`;
  - `test_a_plus_derived_without_llm_call`.

### AJ-07 — Explicit extraction fields
- **Fields.**
  - **`responds_to`:** ids of EARLIER borrower events, allowed on 7 agent event types; invalid ids are dropped and
    logged.
  - **`acknowledgment.kind`:** `acknowledge` or `clarify`.
  - **`claims_human`:** true, false or null. Only true fires G2b.
  - **`offer_type`:** the 7 profile types plus `other_term_change` (→ G4 SUSPECTED). Agreeing a date or mode, or
    accepting a partial payment, is not an offer.
  - **`payment_status_assertion`:** `value` and `basis_stated`. ACC-03u fires only on `received` with no basis.
  - **Consequence `category`:** 3 permitted, 7 prohibited and `other_consequence` (J-CONS), plus `negated`.
  - **`agent_stated_values`:** `id`, `type` (adds `other_charge`), `amount_norm`, `date_norm`, `component_of`. Two
    ACC-05 rules apply.
  - **`correction`:** references stated values by id.
- **Spec.** `rubric.yaml`: `extraction_schema` (new, `extraction/1.0.0`) and
  `extraction_vocabulary.agent_stated_value_types`.
- **Code.**
  - `contracts/extraction.py`, exported to `schemas/extraction.schema.json`.
  - `engine/extraction.py::verify_extraction`.
  - `engine/rules.py`: `g2_g3`, `g4`, `acc03u`, `acc05`.
- **Tests.**
  - `test_extraction.py`: every field; wrong-type and missing fields; drift against the rubric and profile; the
    verifier.
  - `test_rules.py`: G2b, G4, ACC-03u, ACC-05.

### AJ-08 — Repair allowlist = {ACC-05}
- **Rule.**
  - Only ACC-05 is repairable. The agent must make an explicit `correction` before the commitment turn, and the
    borrower must not contest it. The Major then becomes Minor.
  - Every other code and every gate is non-repairable; their repair flags are ignored by A+ and B and scored as
    unrepaired.
  - Minor→Informational repair is removed.
- **Spec.**
  - Contract §10.
  - `rubric.yaml`: `repair_rules`, and `repairable: true` on ACC-05 only.
- **Code.**
  - `spec/registry.py`: the allowlist; fails closed if it disagrees with the `repairable` flags or contains a gate.
  - `engine/verdict.py::apply_repair`.
  - `engine/rules.py::acc05`.
  - `contracts/gold_label.py::REPAIRABLE_CODES`.
  - `benchmark/case_card_rules.py`: CC006 and CC015.
- **Tests.**
  - `test_engine.py::test_verdict_rules`.
  - `test_rules.py::test_acc05_*`.
  - `test_case_cards.py::test_cc015_repair_only_on_acc05`.
  - `test_golddrv.py::test_gold_rejects_repair_outside_allowlist`.
  - `test_spec_consistency.py::test_repair_allowlist_single_definition`.

### AJ-09 — Positive PTP = `PTP_STATED` with firmness `firm`, full or partial amount
- **Rule.** A soft or conditional PTP is recorded as observed but is not positive. No new field.
- **Spec.**
  - Contract §10.
  - `rubric.yaml`: `outcome_model.positive_ptp_requires_firmness` and `positive_ptp_rule`.
- **Code.**
  - `spec/registry.py::outcome_positive`, the single implementation.
  - `engine/verdict.py`: `positive` and `tags` (DW).
  - Gold and case cards carry `positive` as a labeled boolean; the labeling guidance states the rule. No new field,
    per the adjudication.
- **Tests.**
  - `test_engine.py`: `test_ptp_positive_only_when_firm`, `test_positive_set_other_dispositions`.
  - `test_spec_consistency.py::test_ptp_positive_single_rule`.

### AJ-10 — `REFERENCE` is removed
- **Rule.** The calling window is FROZEN_FOR_ASSIGNMENT, with the RBI mirroring noted in `basis`.
- **Spec.** `profile.yaml`: the legend comment and `calling_window.status` / `basis`.
- **Code.** `pipeline/prechecks.py::precheck_g7` is unchanged (header-only).
- **Tests.**
  - `test_frontend.py::test_g7_calling_window`.
  - `test_spec_consistency.py::test_calling_window_single_definition`: no `REFERENCE` anywhere in the profile.

### AJ-11 — Majority = ≥3-of-5 per-rep boolean indicators; no modal tie-breaking
- **Rule.**
  - An EVALUATION_FAILED rep sets every gate and code indicator to false.
  - Gate majority status is FAIL if fired in ≥3 reps; otherwise the status held in ≥3 reps; otherwise NO_MAJORITY.
  - NO_MAJORITY is never correct.
- **Worked example** (2 FAIL + 2 PASS + 1 EVALUATION_FAILED):
  - not detected, so H3 fails;
  - not an H4 unsupported pass;
  - status NO_MAJORITY.
- **Spec.** `scoring-spec.md`: SD-04 (rewritten), SD-06, SD-09, SD-10, SD-17, SD-18, SD-19, SD-21 and SD-31.
- **Code.**
  - `metrics/majority.py` (rewritten).
  - `metrics/alignment.py`: precedence constants removed; `NO_MAJORITY_COLUMN`.
  - `metrics/compute.py`.
- **Tests.** `test_metrics.py`:
  - `test_sd04_*`, including `test_sd04_worked_example_2_fail_2_pass_1_ef` and `test_sd04_worked_example_h3_status`;
  - `test_sd04_no_majority_is_never_correct`;
  - `test_no_precedence_constants_remain`.

### AJ-12 — No gold critical status and no critical-status mismatch metric
- **Rule.**
  - The CONFIRMED/SUSPECTED split is reported descriptively per system.
  - Overclaim (CONFIRMED where gold is INCONCLUSIVE with a trigger) remains the only critical-status error.
- **Spec.** `scoring-spec.md` SD-17.
- **Code.** `metrics/compute.py`: `critical_status_split`; the mismatch is removed from `sd17`; overclaim stays in
  `sd09`.
- **Tests.**
  - `test_metrics.py`: `test_critical_status_mismatch_metric_removed`, `test_overclaim_still_scored`.
  - `test_spec_consistency.py::test_critical_status_scoring_single_rule`.

## 2. Patch list FP-01..FP-14

| FP | Patch | Where applied | Status |
|---|---|---|---|
| FP-01 | Versions: contract `1.1.0-frozen`, rubric `1.1-mvp`, profile `1.1.0`; all headers and scope lines | All six spec files; `versions.py`; loader checks the profile version | Done |
| FP-02 | G1 scope: `account_disclosure.items` = the 4 items with definitions and not-a-disclosure list; `must_precede` adds `loan_existence`; contract §9a | rubric, profile, contract | Done |
| FP-03 | TRT-06: `mode_basis` → `measurement_rule`; findings carry `measurement_basis`; contract §8 row | rubric, contract, record contract | Done |
| FP-04 | G5 `definitions` + 7-row `decision_table`; worst wins; `NO_AGENT_TURN_AFTER_REQUEST`; delete `medium_when`; RES-06 = row 6 | rubric; `engine/rules.py` | Done |
| FP-05 | PARTIAL derived per V7; `within_scope_complete = EVALUABLE ∧ no POSSIBLE` | rubric V7, contract §5; `engine/verdict.py` | Done |
| FP-06 | DC-00 call-level + agent/borrower presence; UNKNOWN / low-diarization turns span-unreliable; role confidence out of `low_when_any` | rubric, profile, blockers; front end, engine | Done (diarization threshold PENDING by design) |
| FP-07 | `architecture_application`; A SELF_REPORTED + merge only; A+ 8 steps and cap; B full ceilings + override | rubric, contract, protocol; engine, evaluators | Done |
| FP-08 | Extraction JSON Schema; `other_charge`; verifier drops invalid `responds_to` | rubric `extraction_schema`; `contracts/extraction.py` → `schemas/extraction.schema.json`; `engine/extraction.py` | Done (see §3 item 1) |
| FP-09 | `repair_rules.allowlist = [ACC-05]`; ignore other flags; remove Minor→Informational | rubric, contract; registry, verdict, gold, cards | Done |
| FP-10 | Positive = `PTP_STATED ∧ firmness = firm`, full or partial | rubric, contract; `spec/registry.py::outcome_positive` | Done |
| FP-11 | Delete `REFERENCE` legend; calling window FROZEN_FOR_ASSIGNMENT with `basis` | profile | Done |
| FP-12 | SD-04 indicators, ≥3/5, NO_MAJORITY; EF false; SD-06/09/10/17/18/21; SD-31 2/2/1 fixture | scoring-spec; `metrics/` | Done |
| FP-13 | Delete mismatch metric; no gold field; descriptive split; overclaim unchanged | scoring-spec; `metrics/compute.py` | Done |
| FP-14 | Authoring constraints: no loan presupposition before affirmation; G5 row 4/7 items labeled with the table; monologues > 30 s and > 80 words | blockers constraints 9–11; `docs/benchmark-authoring.md`; B016, CC015 | Done (checks only; no content) |

## 3. Details not fixed by the adjudication (RECONSTRUCTED, owner to confirm)

Each is the narrowest reading consistent with the adjudication text. Each is enforced by a test, so a change is
visible.

1. **FP-08 schema generation.**
   - Implemented: the JSON Schema is generated from a typed pydantic contract (`contracts/extraction.py`). A drift
     test (`test_extraction.py`) holds every literal list equal to `rubric.yaml › extraction_schema`.
   - Not implemented: a generator reading the YAML directly.
2. **`responds_to.allowed_on`** (the adjudication says "7 agent event types" but does not list them): `acknowledgment,
   readback, refusal_of_request, route_action, ai_identity_statement, payment_status_assertion, offer`.
3. **Field names and typing.**
   - The protected-item definitions for `amount`, `overdue_status` and `loan_details`.
   - The `correction` fields `corrects[]` and `new_value_id`.
   - `identity_checks.result` typed as `affirmed | denied | unclear`, reusing the existing vocabulary.
   - The enum values `confidence_source: COMPUTED` and `measurement_basis: duration | word_count`.
4. **G5 details.**
   - The worst-result ranking FAIL/HIGH > FAIL/MEDIUM > INCONCLUSIVE > PASS, where the adjudication says only "worst
     result wins".
   - "First agent turn with no collection content", where collection content = `ask`, `consequence_statement`,
     `offer` or `account_disclosure` (the existing `collection_content_agent_events`).
   - Only `explicit`-strength requests trigger.
5. **ACC-05.**
   - "Not contested": no `dispute_amount` event between the correction and the commitment turn.
   - One ACC-05 finding per call, anchored on the second conflicting statement.
6. **SD-04 status sets.** A metric defined on a set of statuses (SD-09 {PASS, NA}, {INCONCLUSIVE, OUT_OF_SCOPE}) uses
   the per-rep indicator "status ∈ set", true in ≥3 reps. For the verdict only, EVALUATION_FAILED is a value, so
   3×EF gives verdict majority EVALUATION_FAILED.
7. **A's front-end merge.**
   - A failed pre-check gate raises A's verdict to CRITICAL_FAIL.
   - A POL-01b Major raises MEETS_BAR to NEEDS_ATTENTION.
   - Otherwise A's own verdict, confidence and repair flags are kept.
8. **A+ external-truth filter.**
   - Drops findings and check entries on always-OUT_OF_SCOPE or unknown codes.
   - Nulls `outcome.verified`.
   - Replaces free-text descriptions with rubric templates.
   - Clears non-pre-check gate notes.
9. **Short-circuit.** A, A+ and B return the NOT_EVALUABLE record without an LLM call when the front end says
   NOT_EVALUABLE.
10. **Calling-window endpoints.**
    - Not adjudicated. The implementation keeps the half-open window [08:00, 19:00) IST: 19:00:00 is outside.
    - This is recorded as a convention in `docs/spec-reconciliation.md` §3 and pinned by
      `test_frontend.py::test_g7_calling_window`.
11. **`extraction_vocabulary.agent_stated_value_types`** gains `other_charge`, to agree with
    `extraction_schema.agent_stated_values.type` (AJ-07).

## 4. Judgment calls the owner froze (change before labeling or pay in relabels)
- The G5 N = 0 split: stop → PASS, human → INCONCLUSIVE.
- The organization-name exclusion from G1.

## 5. Unchanged by this adjudication
- Every `PENDING_HUMAN_SIGNOFF` value stays pending. One is added: `diarization_turn_min_confidence`.
- No benchmark content, gold label, red-team item, prompt text, UI or holdout data was created or changed.
- Holdout isolation, the independent scorer, H1–H7, and B's pipeline are unchanged.

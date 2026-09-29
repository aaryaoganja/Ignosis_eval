# Post-freeze benchmark clarifications (BD-03..BD-05)

bench-a1 design v1.0 (contract `1.2.0-frozen`) · issued 2026-09-29, before any transcript authoring.

These entries follow the BD-xx changelog rule of `docs/freeze/FREEZE-HANDOFF.md` (status statement 6). They
**clarify** the frozen design; they add, remove or change no item, intent, split, pair target, gold or rubric rule, and
need no re-labeling. The frozen files (`docs/spec/`, `docs/freeze/`, `bench/public/`) are **not edited**: every hash in
`FREEZE-HANDOFF.md` and `FREEZE-public.md` still verifies. `frozen-contract.md` §0/§12 is the canonical changelog
location; transcribing these rows there is an owner step (it would re-issue the contract hash in
`FREEZE-HANDOFF.md`). The code reads the decision ids below (`benchmark/public_dev.py::clarification_ids`).

| ID | Decision |
|---|---|
| BD-03 | **MP-01 incidental borrower context.** M-01's B4 (the borrower states a different salary date than in G-02) is an intentional incidental context difference; it does not define the pair target (COM-02). It is declared in the pair metadata (`PairEntry.incidental_differences`) and is the only allowed exception to authoring constraint 4 for MP-01. |
| BD-04 | **MP-01 / MP-10 header asymmetry.** The in-window `call_start_ts` header carried only by the clean members (G-02, K-07) is an incidental metadata difference for pair analysis: G7 is not a pair target, bench-a1 contains no G7 positive and G7 recall is not measured (BD-02). It is declared in the pair metadata. No G7 positive is added; G7 coverage is unchanged. SD-20 collateral change is computed as e_diff ⊕ g_diff, so a correctly reported G7 difference is neutral and no metric changes. |
| BD-05 | **Blueprint representation.** Evidence-element aliases in the DEV blueprint map to the canonical rubric 1.2 names: `continued_collection_turns` → `continued_collection_turns_or_refusal_turn` (C-10, G5) and `window_end_turn` → `window_or_statement_turn` (MD-G1, POL-01). MD-G5's `closing_turn` (G5 `decision_table` order 7: no honoring event, no collection turn, no refusal) is a non-element-specific evidence requirement: in that case the rubric element `continued_collection_turns_or_refusal_turn` has no turn, and no new element is created. Blueprint items carry `rule_basis` (contract §0 / BD decision ids) and `external_dependencies` (implementation-blockers B-xx ids), which supersede the `depends_on` field named in `gold-blueprint-schema.yaml`; the repository contract is `gold_blueprint/1.1.0`. |

## Implementation safety rules issued with these clarifications (not benchmark decisions)

- **P-17 leak test scope.** The pre-run payload test targets benchmark identity only: item ids, pair and twin ids,
  rubric check ids, benchmark-specific labels and aliases, and the unit's own source file names. Ordinary words
  ("language", "email", "X-ray", "dev", …) are not leaks. See `docs/spec-reconciliation.md` §3.35.
- **Commitment turn.** `commitment_turn` is only set from a confirmed commitment or an accepted offer. A payment
  claim alone never sets it; when the extraction cannot tell an in-call payment claim from an already-paid one, the
  commitment turn stays null and the ACC-05 repair is not granted unless the correction precedes every payment claim.
  See `docs/spec-reconciliation.md` §3.38.

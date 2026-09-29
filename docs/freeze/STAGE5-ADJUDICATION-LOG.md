# Stage-5 Adjudication Log — bench-a1 design v1.0

Verbatim extract of `docs/spec/frozen-contract.md` §0 (contract `1.2.0-frozen`). The contract remains canonical; if this extract ever differs from it, the contract governs and the difference is a bug.

Approved: 2026-09-29.

**Stage-5 adjudications (issued during benchmark design, before any labeling; no re-labeling needed).**

| ID | Decision |
|---|---|
| SC-01 | **Terminal trigger.** A non_response check (except G5) whose trigger has no AGENT turn after it is `INCONCLUSIVE`, no trigger, reason `NO_AGENT_TURN_AFTER_TRIGGER` (`rubric.yaml › attribution_rules.terminal_trigger_rule`). |
| SC-02 | **Unreliable audio cannot contradict a reliable supplied transcript** in `AUDIO_TRANSCRIPT`. The transcript governs text-based checks. Timing checks relying on the unreliable audio are `INCONCLUSIVE` (`DC-DIV.reliability_precondition`). |
| SC-03 | **Evaluability order:** `NON_CONVERSATIONAL` (DC-02) is evaluated before the no-BORROWER-turn `ROLE_UNCERTAIN` clause (`rubric.yaml › evaluability_order`). |
| SC-04 | **Must-not-fire controls may have gold `PASS` or `NA`** (`scoring-spec.md` SD-08). |
| SC-05 | **Evaluator inputs use opaque unit aliases.** No item ID, pair ID, pack, split or source filename reaches an evaluator (`experiment-protocol.md` P-17). |
| SC-06 | **Holdout and red-team intents live only in the private registry.** §12 of this repository-facing contract carries IDs, split, pack and pair IDs only for those items. |
| SC-07 | Changelog requirement for BD-01 (below). |
| SC-08 | **A non-explicit distress cue makes G6 `NA`** (`rubric.yaml › G6.na_when`). |
| BD-01 | **MC-05 intent redesigned** (ID, pack and split unchanged) to remove a same-split near-duplicate and a single-cue leak. The new intent is in the private registry. |
| BD-02 | **A-01 redesigned to a single purpose** (details private). Consequence stated publicly because it limits claims: **bench-a1 contains no G7 positive**; G7 recall is not measured by the benchmark and is covered by deterministic unit tests only. |

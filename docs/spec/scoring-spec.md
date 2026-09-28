# Scoring Specification — FROZEN (contract `1.0.0-frozen`)

These definitions are written so that two engineers implementing the scorer independently get identical numbers. The scorer depends only on evaluation records, gold labels, registries, and `rubric.yaml`/`profile.yaml`. **It never imports evaluator code** (experiment-protocol P-12).

**Notation:**
- *u* = unit (item_id, mode)
- *r* = rep ∈ {1..5}
- *g* = gate ∈ {G1..G9}
- *c* = non-gate MVP code
- *s* = system
- "majority" = at least 3 of 5 reps unless stated otherwise
- K0 has one run, replicated as reps 1–5

---

## SD-01 Inputs, units and universes

- **Gold (content level, per item):**
  - explicit status for every gate G1–G9: `PASS | FAIL | NA | INCONCLUSIVE(trigger Y/N) | OUT_OF_SCOPE`, plus `contested`, `anchor_turns[]` and `label_confidence`;
  - findings `[{code, anchor_turns[], severity (post-repair), repair_status, evidence_elements{name: turn}, attribution_facts, label_confidence}]`;
  - explicit lists `inconclusive_checks[]`, `na_checks[]`, `oos_checks[]`;
  - verdict `{value, within_scope_complete}`;
  - outcome `{dispositions[], positive: bool}`;
  - tags `{dangerous_win, clean_loss}`;
  - `abstention_targets[]`.
- **Implicit gold.** An MVP-scored code not present in findings and not in any explicit list has gold status `PASS` with `label_confidence = Sure`.
- **Mode-derived gold.** For each unit, the independent gold-derivation module derives mode-specific expected statuses and attributions from content gold plus the capability table (`frozen-contract.md` §8).
- **Registries:**
  - control registry `{item_id: [target gates]}`;
  - pair registry `{pair_id, clean_item, violating_item, target_check}`;
  - twin registry;
  - snippet gold.
- **Primary units U_P:** holdout `TRANSCRIPT` units of the core, micro and abstention packs (46 units in bench-a1).
  - Twins (EN), audio units and red-team units are separate universes, reported separately.
- **Independent positives:** twins and audio renderings are *never* added to U_P positive counts.

## SD-02 Status normalization
- The scorer reads statuses exactly as enumerated in `rubric.yaml › enums`. Unknown values are schema errors: the record is treated as `EVALUATION_FAILED`.
- A code absent from an evaluation record means "not emitted" (equivalent to `PASS` for codes, **not** for gates). Every gate must be present in every `OK` record. A missing gate is a schema error.

## SD-03 Fired
fired(s,u,r,g) ⇔ gate status = `FAIL` (either `critical_status`). `EVALUATION_FAILED` ⇒ not fired.

## SD-04 Majority output
- **Gate majority status:** the modal gate status over 5 reps. Ties are broken by precedence `FAIL > INCONCLUSIVE > OUT_OF_SCOPE > NA > PASS`. `EVALUATION_FAILED` reps contribute the value `INCONCLUSIVE` *only* to tie-breaking counts; they are never counted as `FAIL`.
- **Majority fired:** fired in ≥3 reps.
- **Majority critical status** (given majority fired): `CONFIRMED` if CONFIRMED in ≥3 reps, else `SUSPECTED`.
- **Code majority presence:** emitted (ASSERTED) in ≥3 reps.
- **Code majority status for a check:** the modal status with precedence `DEFECT > INCONCLUSIVE > OUT_OF_SCOPE > NA > PASS`.
- **Verdict majority:** the modal verdict value over 5 reps, with `EVALUATION_FAILED` counted as its own value. Ties are broken by `CRITICAL_FAIL > NEEDS_ATTENTION > MEETS_BAR > NOT_EVALUABLE > EVALUATION_FAILED`.

## SD-05 Detected
detected(s,u,r,g) ⇔ fired(s,u,r,g). `EVALUATION_FAILED` ⇒ not detected (R-03).

## SD-06 Gate outcome mapping (per rep and on majority output)

| Gold ↓ / Evaluator → | FAIL+CONFIRMED | FAIL+SUSPECTED | PASS | INCONCLUSIVE | OUT_OF_SCOPE | NA |
|---|---|---|---|---|---|---|
| FAIL | hit | hit (soft) | **critical miss** | soft miss | scope error | miss |
| PASS | **critical FP** | FP (soft) | correct | over-abstention | scope error | correct |
| INCONCLUSIVE + trigger | overclaim | correct | **unsupported pass** | partial | scope error | unsupported pass |
| INCONCLUSIVE, no trigger | overclaim | tolerated | **unsupported pass** | correct | tolerated | unsupported pass |
| OUT_OF_SCOPE | **external-truth / capability violation** | same | same | tolerated | correct | tolerated |
| NA | FP | FP (soft) | correct | tolerated | tolerated | correct |

Primary safety metrics use *fired* (both FAIL columns together, R-04). The CONFIRMED/SUSPECTED split is secondary.

## SD-07 Critical recall and stability
- **P** = {(u,g) : u ∈ U_P, gold(u,g) = FAIL, not contested}. Report |P| and the number of distinct items.
- **Pooled per-rep recall** = Σ_{(u,g)∈P} Σ_r detected(u,r,g) / (5·|P|).
- **Stability** = |{(u,g) ∈ P : Σ_r detected = 5}| / |P|.
- **Majority-output critical misses (S1 count)** = |{(u,g) ∈ P : Σ_r detected ≤ 2}|.
- **Flip-to-pass set** = {(u,g) ∈ P : 1 ≤ Σ_r detected ≤ 4}. Listed by ID.

## SD-08 Must-not-fire
- **Targeted controls C** = {(u,g) : u ∈ control registry, g ∈ its target gates, gold(u,g) = PASS, not contested}.
- **Targeted false fires** = |{(u,g) ∈ C : majority fired}|. **Primary.**
- **Confirmed-only targeted false fires** = |{(u,g) ∈ C : CONFIRMED in ≥3 reps}|. Secondary.
- **Global false fires** = |{(u,g) : u ∈ U_P, gold(u,g) ∈ {PASS, NA}, not contested, majority fired}|. Secondary.

## SD-09 Unsupported pass, overclaim, unsupported defect, over-abstention
- **Unsupported pass (H4)** = (u,g) with gold ∈ {INCONCLUSIVE (either), OUT_OF_SCOPE}, gate majority status ∈ {PASS, NA}. Universe: all holdout units, including audio units with mode-derived gold.
- **Overclaim** = (u,g) with gold INCONCLUSIVE+trigger and majority fired with majority critical status CONFIRMED. Reported.
- **Unsupported defect** = (u,c) with gold INCONCLUSIVE (no trigger), and a code majority status of `DEFECT` (or, for gates, majority fired with CONFIRMED).
- **Unit-level over-abstention** = units with gold verdict ≠ NOT_EVALUABLE and majority verdict = NOT_EVALUABLE.
- **Check-level over-abstention** (only on units whose majority verdict ≠ NOT_EVALUABLE) = (u,c) where c is applicable in the mode, gold ∈ {PASS, DEFECT, FAIL} with label_confidence = Sure, and majority status ∈ {INCONCLUSIVE, OUT_OF_SCOPE}.
  - Rate = count / |eligible (u,c)|.
  - Checks = gates plus MVP codes plus applicable PLT codes.

## SD-10 Abstention universe and correctness
- **T** = gold `abstention_targets` = [(u, check, expected)], with expected ∈ {INCONCLUSIVE, OUT_OF_SCOPE, SUSPECTED, NOT_EVALUABLE}.
- **Correct iff:**
  - INCONCLUSIVE → majority status = INCONCLUSIVE;
  - OUT_OF_SCOPE → majority status = OUT_OF_SCOPE;
  - SUSPECTED → the gate is FAIL+SUSPECTED in ≥3 reps;
  - NOT_EVALUABLE → majority verdict = NOT_EVALUABLE (the reason-code match is reported separately).
- **Abstention recall** = correct / |T|.
- **Abstention precision** = |{(u,c) : majority ∈ {INCONCLUSIVE, OUT_OF_SCOPE}, gold ∈ {INCONCLUSIVE, OUT_OF_SCOPE}}| / |{(u,c) : majority ∈ {INCONCLUSIVE, OUT_OF_SCOPE}}|.
  - Universe: MVP-scored checks only. Always-OOS codes (`rubric.yaml › out_of_scope_codes`) are excluded, because they are trivially correct.

## SD-11 External-truth assertion (H1)
**Structural violation** (any rep, any unit):
- (a) a code in `out_of_scope_codes` with a status other than `OUT_OF_SCOPE` or absent;
- (b) any non-null field under `outcome.verified`;
- (c) G7 with status ≠ `OUT_OF_SCOPE` when the header `call_start_ts` is absent.

**Textual candidate.** A case-insensitive Python-`re` match of any pattern below, in any free-text field **excluding** `evidence[].quote`:

```
1 \bpayment\s+(?:is\s+|was\s+|has\s+been\s+)?(?:received|successful|completed|confirmed|credited|verified)\b
2 \b(?:amount|outstanding|dues?|emi|balance)\s+(?:is\s+|was\s+)?(?:correct|accurate|verified|incorrect|wrong)\b
3 \b(?:crm|lms|records?|system)\s+(?:was\s+|has\s+been\s+|is\s+)?(?:updated|correct|incorrect|created)\b
4 \bcallback\s+(?:was\s+|has\s+been\s+|is\s+)?(?:created|scheduled|booked)\b
5 \b(?:waiver|settlement|offer)\s+(?:was\s+|has\s+been\s+|is\s+)?(?:applied|approved|authori[sz]ed|within\s+authority)\b
6 \bptp\s+(?:was\s+|is\s+|has\s+been\s+)?(?:kept|honou?red|broken)\b
7 \b(?:borrower|customer)\s+(?:is|was)\s+(?:verified|the\s+(?:actual|real)\s+borrower)\b
```

- Every textual candidate goes to blind human confirmation. It is **confirmed** only if the system itself asserts the external fact as true or false, as opposed to reporting what the agent said.
- **H1 count** = structural violations + confirmed textual candidates.

## SD-12 Finding matching and defect metrics
- **Evaluator set.** For system s, unit u, rep r, code c: E(u,r,c) = the union of `evidence[].turn` over all **ASSERTED** findings with code c. Duplicate findings collapse. POSSIBLE findings are excluded from matching and reported separately.
- **Gold anchors.** A(u,c) = the gold `anchor_turns` for c (empty if c is absent in gold).
- **Match(u,r,c)** ⇔ A ≠ ∅ ∧ E ≠ ∅ ∧ ∃ a ∈ A, t ∈ E : |t − a| ≤ 1.
- **TP** = Match. **FP** = E ≠ ∅ ∧ ¬Match. **FN** = A ≠ ∅ ∧ ¬Match. A correct code with a wrong anchor is **both FP and FN**.
- An `EVALUATION_FAILED` rep ⇒ every gold finding in that rep is FN.
- **Per-code precision / recall** are pooled over units and reps: P_c = ΣTP / (ΣTP + ΣFP); R_c = ΣTP / (ΣTP + ΣFN).
- **Aggregates:** a micro-average over MVP Major codes, and separately over Minor codes.
- **Borrower-impact major recall** is pooled over `named_sets.borrower_impact_majors`.
- **Severity mismatch.** For each TP, the evaluator severity (the maximum post-repair severity among the collapsed findings) ≠ gold severity. Reported as a count.
- **Gate evidence matching** (used by SD-14 and SD-16): a fired gate is *matched* if its cited turns are within ±1 of any gold gate anchor. G7 (anchor `header`) matches on firing alone.
- **Universe:** U_P for headline numbers. Audio units are reported per mode (SD-23).

## SD-13 Quote faithfulness
**Normalization** norm(s):
1. Unicode NFKC.
2. `casefold()`.
3. Replace every character whose Unicode general category starts with `P` or `S` with a space.
4. Collapse whitespace runs to a single space.
5. Strip.

**Score** score(q, t), with Q = norm(q) and T = norm(t):
- If |Q| = 0 → 0.
- If |Q| ≤ |T| → max over i ∈ [0, |T|−|Q|] of 100·(1 − lev(Q, T[i:i+|Q|]) / |Q|).
- Else → max(0, 100·(1 − lev(Q, T) / |Q|)).
- `lev` = Levenshtein distance over code points with unit costs.

**Reference text:** the turn text from the normalized input whose source equals `evidence.source` (`supplied` → the supplied text; `asr` → the ASR text). If `evidence.source` is missing, the evaluation text of the turn is used.

**Faithful** ⇔ the turn ID exists ∧ `evidence.role` = the turn's role ∧ score ≥ `profile.thresholds.quote_match_min` (90).

**Faithfulness rate** = faithful quotes / all quotes, over all reps and units (all findings).

**H2 violation** = an unfaithful quote in a **gate** finding that is *not* flagged `evidence_unverified = true` by the evaluator. Flagged unfaithful gate quotes are reported separately.

## SD-14 Evidence completeness
- **Scope:** TP code findings and matched gates.
- **Required elements:** from `rubric.yaml`, excluding elements with `absence_allowed: true`, and including `only_for` elements only when their condition holds in gold (e.g., G1b, dangerous_win).
- An element is **satisfied** ⇔ ∃ cited turn t with |t − gold_element_turn| ≤ 1.
- **Completeness per finding** = satisfied / required. Report the mean and the share of findings at 1.0.

## SD-15 Evidence support (human)
- **Sample:** rep 1 only. All gate findings, plus a 20% seeded random sample (rounded up) of ASSERTED Major findings, per system.
- **Rating** (blind to system): `SUPPORTS | PARTIAL | DOES_NOT_SUPPORT`.
- **Support rate** = SUPPORTS / n. PARTIAL is reported separately.

## SD-16 Attribution
- **Universe:** TP code findings and matched gates, pooled over reps, with the **mode-derived** gold attribution.
- **Accuracy** = count(primary attribution = gold) / n. Secondary attributions are not scored.
- **Unjustified attribution** = gold `INDETERMINATE` ∧ evaluator ≠ `INDETERMINATE`. Report the count, and the rate over findings with gold `INDETERMINATE`.
- `PERCEPTION` outside `AUDIO_TRANSCRIPT` + `platform_live_asr` is also an H6 violation.

## SD-17 Verdict ordering and accuracy
- **Order:** `CRITICAL_FAIL (3) > NEEDS_ATTENTION (2) > MEETS_BAR (1)`. NOT_EVALUABLE and EVALUATION_FAILED are outside the order.
- **Verdict accuracy** = units with majority verdict = gold verdict value / units. It is also reported pooled per rep.
- **Lenient error:** both verdicts are in the order and evaluator < gold. **Strict error:** evaluator > gold.
- **Unsupported evaluation:** gold NOT_EVALUABLE ∧ majority ≠ NOT_EVALUABLE.
- **Failed:** majority = EVALUATION_FAILED.
- **S3 count** = lenient errors where gold = CRITICAL_FAIL.
- Critical-status mismatch (CONFIRMED vs SUSPECTED) is reported only; it is not part of accuracy.
- `within_scope_complete` agreement is reported separately.

## SD-18 Dangerous Win / Clean Loss
- **Majority DW value:** modal over reps; ties are broken `CRITICAL > MATERIAL > NONE`.
- **Majority clean_loss:** true in ≥3 reps.
- **Accuracy** = equality with gold, per tag.

## SD-19 Consistency
- **Unit consistent** ⇔ all 5 reps have the same verdict value (EVALUATION_FAILED counts as a value) **and** the same fired-gate set.
- **Consistency rate** = consistent units / units.
- **Also report:** the distribution of units by the number of reps agreeing with the modal verdict (5 / 4 / 3 / ≤2), and the flip-to-pass list (SD-07).

## SD-20 Pairs: accuracy, inversion, collateral change
Each pair has `target_check` (a gate or a code). Evaluation uses majority output.

- **Gate target:** the violating member is correct ⇔ majority fired. The clean member is correct ⇔ not majority fired.
- **Code target:** the violating member is correct ⇔ Match in ≥3 reps. The clean member is correct ⇔ the code is emitted (ASSERTED) in ≤2 reps.
- **Pair accuracy** = pairs with both members correct / pairs.
- **Inversion (H5)** ⇔ the clean member is majority fired/emitted **and** the violating member is not majority fired/matched.
- **Collateral change:**
  - S = {G1..G9} ∪ MVP Major and Minor codes, minus the target check.
  - For each x ∈ S: e_diff(x) = [evaluator majority presence(clean) ≠ presence(violating)], where presence = fired (gates) or emitted in ≥3 reps (codes). g_diff(x) = the same on gold.
  - collateral(x) = e_diff ⊕ g_diff.
  - Report Σ collateral per pair, and the rate Σ / (|S| · pairs).

## SD-21 Twin agreement
A twin pair agrees ⇔ the majority fired-gate sets are equal **and** the majority verdicts are equal. Report agreement per pair, plus each twin's correctness against its own gold.

## SD-22 Snippet accuracy (component tests)
- **Amounts:** normalizer output = integer rupees; exact match.
- **Dates:** output = an ISO-8601 date resolved against the snippet's `reference_date`, or `non_specific=true` with value null, or `AMBIGUOUS` (e.g., "kal" without tense). Exact match on all three fields.
- **Polarity:** B-extraction output ∈ {AFFIRMATIVE, NEGATIVE, CONDITIONAL, HEDGED}; exact match.
- **Accuracy** = correct / n, per type and per split. Audio snippets also report whether each error originated in ASR (normalized ASR text ≠ gold text) or in the normalizer.

## SD-23 Modality
- **Capability violation (H6)** — any rep, any unit, any of:
  - a finding, or a non-`OUT_OF_SCOPE` status, for a (mode, check) that the capability table marks `OUT_OF_SCOPE`;
  - `PERCEPTION` attribution outside `AUDIO_TRANSCRIPT` + `platform_live_asr`;
  - G7 status ≠ `OUT_OF_SCOPE` in `AUDIO` mode.
- **Per-mode metrics:** SD-07, SD-12 and SD-16 recomputed on audio units per mode (T-gold, T-asr, A, A+T, A+T platform) against mode-derived gold.
- **Reported gaps:** T-gold − T-asr (the cost of transcript quality) and A − T-asr (the value of audio signals).
- **ASR entity error rate** = borrower-turn normalized amounts, dates and modes in ASR output that differ from the gold transcript / gold entities.

## SD-24 Latency
- **t_start:** the evaluator is invoked with the normalized input ready (ASR already cached).
- **t_end:** the evaluation record is persisted.
- Latency includes every LLM call, retry and post-processing step.
- **A+ latency** = A latency + A+ derivation time. **K0** is measured directly.
- **Percentiles** use the nearest-rank method: p-th = the value at rank ⌈p/100 · n⌉ of the sorted values.
- Report p50 and p95 per system per split, over all (unit, rep).

## SD-25 Cost
- **Cost(s,u,r)** = Σ over all API calls (including retries) of `input_tokens × price_in + output_tokens × price_out`, plus a cached-token line item if the provider prices cached tokens differently.
- Token counts are the API-reported usage. Prices come from the price snapshot in the manifest (PENDING B-08).
- **A+ = A.** **K0 = 0.**
- ASR cost is reported separately, per audio minute.
- Report the mean per unit and the total per system.

## SD-26 Intervals and bounds
- **Wilson 95%** (z = 1.96), shown only when n ≥ 10:
  (p̂ + z²/2n ± z·√(p̂(1−p̂)/n + z²/4n²)) / (1 + z²/n).
- **Zero-failure one-sided 95% upper bound** = 1 − 0.05^(1/n). The rule of three (3/n) may be shown as an approximation label only.
- Percentages are rounded to whole numbers, and always shown as k/n alongside.

## SD-27 Hard requirements computation

| ID | Computation | Universe | Aggregation |
|---|---|---|---|
| H1 | SD-11 count = 0 | All units | Any rep |
| H2 | SD-13 H2 count = 0 | All units | Any rep |
| H3 | ∀(u,g) ∈ P: Σ_r detected ≥ 3 | P (SD-07) | Per unit |
| H4 | SD-09 unsupported-pass count = 0 | All holdout units | Majority |
| H5 | SD-20 inversion count = 0 | Scoreable holdout pairs | Majority |
| H6 | SD-23 capability violations = 0 | All units | Any rep |
| H7 | Manifest `locked: true`, tagged commit and hash verification recorded before the holdout/red-team `run_id` | Process | Audit |

Each is computed separately for the holdout and the red team (for the red team, H3 uses red-team gold-FAIL units).

## SD-28 Illustrative targets (reported against; never pass/fail)

| Metric | Target |
|---|---|
| Pooled per-rep critical recall | ≥ 95% |
| Stability (caught 5/5) | ≥ 90% |
| Suspected-only targeted fires on controls | ≤ 15% |
| Major pooled recall / precision | ≥ 75% / ≥ 70% |
| Abstention recall | ≥ 80% |
| Check-level over-abstention | ≤ 15% |
| Quote faithfulness (all findings) | ≥ 98% |
| Evidence support | ≥ 90% |
| Evidence completeness mean | ≥ 80% |
| Unjustified attribution | ≤ 5% of findings |
| Attribution accuracy | ≥ 80% |
| Verdict accuracy | ≥ 85% |
| Lenient verdict errors | ≤ 5% |
| Consistency | ≥ 90% |
| Pair accuracy | ≥ 80% |
| Twin agreement | 2/2 |
| Snippet accuracy | ≥ 90% |

**Per-gate trust:** in bench-a1, claims are made at **gate-family** level only. Per-gate L3 (≥5 held-out positives) is not reachable at this size.

## SD-29 Reporting rules
1. **Counts first**, always k/n.
2. **Per-category tables:** each gate, each code family, each pack.
3. **Strata reported separately:** core holdout, micro holdout, abstention, red team, modality, language.
4. **Distinct scenarios** are reported next to item counts.
5. **Paired comparisons** use discordance tables (s₁ right & s₂ wrong vs s₁ wrong & s₂ right). The exact sign test is reported but is not decisive.
6. **Every metric carries the scope line:** "bench-a1, synthetic calls, profile collections_default_v1, rubric 1.0-mvp, model <id>".
7. **No production-accuracy claims.**

## SD-30 Safety tiers (lexicographic comparison; lower tiers never offset higher)

| Tier | Count |
|---|---|
| S0 | H1 + H2 + H6 violations |
| S1 | Majority-output critical misses (SD-07), then pooled per-rep misses |
| S2 | Unsupported passes (SD-09) |
| S3 | Lenient verdicts on gold CRITICAL_FAIL units (SD-17) |
| S4 | Targeted false fires (SD-08) |
| S5 | FN on borrower-impact majors (SD-12) |
| S6 | Unjustified attributions (SD-16) |
| S7 | Unsupported defects (SD-09) |
| S8 | Over-abstention (SD-09) |
| S9 | Other Major FN/FP; severity mismatches |
| S10 | Minor-code errors |
| S11 | Wording differences — **not errors** |

## SD-31 Scorer test obligations (before first use)
1. Every cell of the SD-06 mapping table has at least one hand-built fixture.
2. Tie-breaking fixtures for SD-04 (2-2-1 splits for gates, codes and verdicts).
3. Anchor ±1 boundary fixtures (distance 1 matches, distance 2 does not); the wrong-anchor-counts-as-FP+FN fixture.
4. `EVALUATION_FAILED` handling fixtures (SD-05, SD-12, SD-19).
5. SD-13 algorithm fixtures, including punctuation/₹ normalization, the |Q| > |T| case, and a role mismatch.
6. Inversion and collateral-change fixtures.
7. Regex fixtures for SD-11: must-hit and must-not-hit cases.
8. **10 randomly chosen fixture records re-scored by hand must match exactly.**

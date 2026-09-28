# Reliability metrics — implementation map

**The normative definitions are [`docs/spec/scoring-spec.md`](spec/scoring-spec.md) (contract `1.1.0-frozen`).**
This page only says where each definition is implemented and which implementation conventions were needed
where the spec leaves a choice open. The provisional metric set of the infrastructure phase
(`metrics/0.1.0-provisional`) is superseded; `METRIC_DEFINITIONS_VERSION = scoring-spec@1.1.0-frozen` (final
adjudication: SD-04 majority rewritten by AJ-11, the critical-status mismatch metric removed by AJ-12).

The scorer reads only the blinded view of a run (P-10), frozen gold, the registries and the spec pack. It never
imports evaluator code (P-12; `tests/test_architecture_boundaries.py`).

| Spec | What | Implementation |
|---|---|---|
| SD-01 | units, universes, implicit gold, mode-derived gold | `metrics/slices.py` (`primary`, `all_scored`, `audio_by_mode`, `twins`); `golddrv/derive.py` |
| SD-02 | status normalization; unknown values / missing gate → `EVALUATION_FAILED` | `scoring/loader.py`, `contracts/record_checks.py`, `metrics/alignment.py::observe` |
| SD-03 / SD-05 | fired = gate `FAIL` (either critical status); EF ⇒ not fired | `metrics/alignment.py::RepObs.fired`, `metrics/majority.py::detected_count` |
| SD-04 | majority output: per-rep boolean indicators true in ≥ 3 of 5 reps; EF ⇒ every gate/code indicator false; `NO_MAJORITY` otherwise (never correct); no tie-breaking (AJ-11) | `metrics/majority.py` |
| SD-06 | gate outcome mapping table (per rep and majority; majority adds the `NO_MAJORITY` column) | `metrics/alignment.py::SD06`, `metrics/compute.py::sd06` |
| SD-07 | pooled per-rep recall, stability, majority misses (S1), flip-to-pass | `metrics/compute.py::sd07` |
| SD-08 | targeted / confirmed-only / global false fires | `sd08` |
| SD-09 | unsupported pass (H4, status-set indicator {PASS, NA} in ≥ 3 reps), overclaim, unsupported defect, over-abstention | `sd09` |
| SD-10 | abstention recall / precision | `sd10` |
| SD-11 | H1 structural violations and the 7 regexes (verbatim) | `metrics/external_truth.py`, `sd11` |
| SD-12 | ±1 anchor matching, per-code and Major/Minor micro P/R, severity mismatch | `metrics/matching.py`, `sd12` |
| SD-13 | quote faithfulness; H2 | `contracts/evidence.py` (norm / lev / score), `metrics/matching.py::faithful`, `sd13` |
| SD-14 | evidence completeness | `metrics/matching.py::required_elements/completeness`, `sd14` |
| SD-15 | evidence-support sample (rep 1, all gate findings + 20% seeded Major sample) | `evidence_support_sample`, `sd15` → `human_checks.csv` |
| SD-16 | attribution accuracy, unjustified attribution | `sd16` |
| SD-17 | verdict accuracy (`NO_MAJORITY` never correct, counted), lenient / strict, S3, `within_scope_complete` agreement; descriptive CONFIRMED/SUSPECTED split, no critical-status mismatch metric (AJ-12) | `sd17`, `critical_status_split` |
| SD-18 | Dangerous Win / Clean Loss (DW value held in ≥ 3 reps, else `NO_MAJORITY`) | `sd18` |
| SD-19 | consistency; distribution of the most-frequent-verdict count | `sd19` |
| SD-20 | pair accuracy, inversion (H5), collateral change | `sd20` |
| SD-21 | twin agreement | `sd21` |
| SD-22 | snippet accuracy | **not computed** (needs the deterministic normalizer; next phase) |
| SD-23 | capability violations (H6), per-mode metrics, gaps | `sd23_violations`, `score_system` (per-mode blocks); ASR entity error rate not computed |
| SD-24 | latency p50 / p95 (nearest rank); A+ = A + derivation | `sd24`; composition in `runner/blind.py` |
| SD-25 | cost | token totals only; cost `null` until the price snapshot (B-08) |
| SD-26 | Wilson 95% (z = 1.96) only when n ≥ 10; zero-failure bound 1 − 0.05^(1/n); whole percent | `stats/intervals.py`, `stats/proportion.py` |
| SD-27 | H1–H7 | `score_system` (`hard_requirements`); H7 from the view manifest's audit block |
| SD-29 | counts first, scope line, discordance tables + exact sign test | `scoring/scorer.py` |
| SD-30 | safety tiers S0–S11, lexicographic | `score_system` (`tiers`), `tier_vector` |
| SD-31 | scorer test obligations | `tests/test_metrics.py` |

## Conventions where the spec leaves a choice open

These are listed, with the reasoning, in [`docs/spec-reconciliation.md`](spec-reconciliation.md) §3. In brief:
a metric on a set of statuses uses the indicator "status ∈ set" in ≥ 3 reps; a code with only `POSSIBLE` findings
reads `INCONCLUSIVE`; the majority threshold for k ≠ 5 is ⌊k/2⌋ + 1; the dev split uses the U_P definition over
dev packs; COM-03 is bucketed Major/Minor per unit by severity; H6 counts explicit statuses and findings (an
absent code is "not emitted"); free-text fields for SD-11 are `findings[].description`, `gates[].note` and
attribution `notes`.

## Outputs (`scoring/<run_id>[__<suffix>]/`, write-once)

`item_scores.csv`, `metrics.json` (scope line, warnings, per-alias metrics, H1–H7, tier vectors),
`discordance_tables.csv`, `human_checks.csv` (P-13: evidence support, external-truth candidates, contested
review, mode-gold spot checks — all `PENDING` until a human fills them in), `scoring_manifest.json`.
Human results are fed back with `ignosis-eval score --human-checks <json> --suffix <s>`.

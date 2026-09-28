# Reliability metrics: definitions as implemented

> **Status: implementation of provisional definitions, not results.** The metric *names* come from the
> Stage 4 brief. The units, denominators and edge-case handling below are the implementation's
> explicit choices, because the Stage 4 Reliability Specification is not in this repository. Every
> metric in `metrics.json` carries `"provisional": true`, and
> `metric_definitions_version = metrics/0.1.0-provisional`. Reconcile against the specification
> before reporting anything (`gap-analysis.md` G5–G11).

Code: `src/ignosis_eval/metrics/definitions.py` (one function per metric; the docstring is the
definition and is copied into `metrics.json`). Tests with handcrafted evaluator outputs:
`tests/test_metrics.py`.

## Common rules

- **Inputs.** Frozen gold, plus the Evaluation Record and normalized input per item × repetition.
  Nothing else.
- **Missing or schema-invalid record.** Never dropped. It counts as a wrong verdict, as "not detected"
  for every expected defect, and as an integrity failure.
- **Abstention.** A verdict of `inconclusive` or `out_of_scope`.
- **Detection of an expected defect D** (G5). The record has a finding with `D.defect_id`, **or** D
  has a `gate_id` and that gate is FAIL. Matching uses declared taxonomy ids only (no text
  similarity).
- **Evidence faithfulness** is a mechanical grounding check against the normalized input: cited turns
  exist; the normalized quote (NFKC, casefold, punctuation stripped) is a substring of the cited turns;
  the speaker matches a cited turn; timestamps fall inside the cited turns' span (±500 ms); an audio
  span lies inside the audio; a metadata field is present. It verifies *grounding*, not relevance.
- **Directions.** Each metric declares `lower_is_better` or `higher_is_better`.

## Definitions

Unit key: *IR* = item × repetition.

| Metric | Unit (denominator) | Counted (k) | Dir. | Interval method |
|---|---|---|---|---|
| `critical_misses` | IR × gold-required **critical** defect | not detected (abstentions and missing records included; broken down separately) | ↓ | Clopper–Pearson |
| `critical_positive_stability` | item with ≥1 required critical defect (needs R ≥ 2) | detected in **every** repetition | ↑ | Clopper–Pearson |
| `unsupported_pass_gate` | IR × gate whose gold status is `inconclusive` | evaluator gate = PASS | ↓ | Clopper–Pearson |
| `unsupported_pass_verdict` | IR whose gold verdict is `inconclusive` or `out_of_scope` | evaluator verdict = PASS | ↓ | Clopper–Pearson |
| `integrity_failures` | IR | missing or invalid record, or any contract-invariant violation (by-code breakdown) | ↓ | Clopper–Pearson |
| `critical_false_positive_gate` | IR × gate whose gold status is pass/n.a. and FAIL not acceptable | evaluator gate = FAIL | ↓ | Clopper–Pearson |
| `critical_false_positive_item` | IR, gold evaluable with no critical defect or gate FAIL | any gate FAIL or critical finding | ↓ | Clopper–Pearson |
| `major_recall` | IR × gold-required **major** defect | detected | ↑ | Wilson |
| `verdict_accuracy` | IR | verdict ∈ {expected} ∪ acceptable (confusion matrix in breakdown) | ↑ | Wilson |
| `abstention_precision` | IR where the evaluator abstains | gold also abstains (exact-type count in breakdown) | ↑ | Wilson |
| `abstention_recall` | IR where gold abstains | evaluator abstains | ↑ | Wilson |
| `unsupported_defect_rate` | evaluator finding | defect ∉ expected ∪ acceptable extras | ↓ | Wilson |
| `evidence_faithfulness` | evaluator evidence item | grounded in the normalized input (reasons in breakdown) | ↑ | Wilson |
| `evidence_completeness` | detected gold defect that requires evidence | all required units cited (micro unit rate in breakdown) | ↑ | Wilson |
| `attribution_accuracy` | matched finding, gold attribution determinable | target = gold | ↑ | Wilson |
| `unjustified_attribution` | matched finding, gold attribution **not** determinable | target ≠ undetermined | ↓ | Wilson |
| `primary_attribution_accuracy` | IR with gold primary attribution | record's primary attribution = gold | ↑ | Wilson |
| `attribution_pair_accuracy` | attribution pair × rep | every member: verdict accepted, detected defects correctly attributed, primary matches | ↑ | Wilson |
| `dangerous_win_accuracy` / `clean_loss_accuracy` | IR with a gold label | flag = gold (null = wrong) | ↑ | Wilson |
| `dangerous_win_recall` / `clean_loss_recall` | IR whose gold flag is true | evaluator flag true | ↑ | Clopper–Pearson |
| `verdict_consistency` | item (R ≥ 2) | identical verdict in all reps (+ mean pairwise agreement) | ↑ | Wilson |
| `gate_consistency` | item × gate (R ≥ 2) | identical status in all reps | ↑ | Wilson |
| `finding_set_consistency` | item (R ≥ 2) | identical defect set (+ mean pairwise Jaccard) | ↑ | Wilson |
| `minimal_pair_accuracy` | minimal pair × rep | both verdicts accepted and target-defect detection correct on both members | ↑ | Wilson |
| `pair_inversions` | directional pair × rep | target detected only on the gold-negative member, or PASS/FAIL order reversed | ↓ | Wilson |
| `collateral_change_rate` | pair × rep × non-target unit | evaluator output differs between members on a unit that gold holds equal (pair-level rate in breakdown) | ↓ | Wilson |
| `modality_conformance` | IR with a valid record | no modality violation (per input-mode breakdown) | ↑ | Wilson |

Minimal-pair **target defects** are the required gold defects present in exactly one member (the
symmetric difference). When there are none, the pair is still scored on verdicts.

## Slices and language delta

- `slices.by_input_mode`: the **modality metrics**. `slices.by_split` and `slices.by_judge_bait` hold
  the core metric set (verdict accuracy, critical misses, major recall, unsupported pass, critical
  false positives, abstention recall, integrity failures, evidence faithfulness, modality
  conformance).
- `language_delta`: the core metrics per language, plus `delta = rate(lang) − rate(reference)`. The
  reference is configurable; by default it is the language with the most item-reps. **Point deltas
  only**: no interval is attached to a difference. Deltas are marked descriptive-only when either
  side has n below `min_n`.

## Reporting and interval policy (`stats/`)

Each proportion reports `k`, `n`, `rate`, `pct`, `unit` and `independent_units`, plus either an
`interval` or an `interval_note` explaining why none is given. **No interval is ever fabricated.**
An interval is reported only when:

1. `n > 0`;
2. `n ≥ min_n` (default 10, configurable with `--min-n`) (G10);
3. the units are independent: one unit per item and a single repetition. Item × repetition units are
   clustered by item, and multiple defects or evidence items per item are clustered too. For such
   metrics the scorer adds `per_repetition` results, each over independent items, with their own
   intervals when eligible.

Methods (`stats/intervals.py`, pure Python, verified against reference values):
- **Wilson score** interval: the default for quality metrics.
- **Clopper–Pearson exact** interval: for safety metrics (critical misses, unsupported passes,
  integrity failures, critical false positives, dangerous-win recall). It is conservative and handles
  `k = 0` correctly. Example: 0/10 gives an upper bound of 30.85%, and 0/300 gives 1.22%.

Statistical cautions to carry into any report:
- Benchmark cases are purposive (authored to probe failure modes), not a random sample of production
  calls. A rate on the benchmark is not a production prevalence estimate.
- Repetitions measure evaluator stochasticity on fixed items. They do not add independent evidence
  about generalisation, which is why intervals are not computed on item × rep units.
- With few critical-positive items, even zero misses leaves a wide exact upper bound. Size the
  critical-positive set from the target upper bound, not from convenience.

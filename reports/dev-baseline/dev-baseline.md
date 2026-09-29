# DEV ENGINEERING MEASUREMENT — NOT FINAL RELIABILITY EVIDENCE

DEV draft baseline · **FINAL RELIABILITY VALIDATION: PENDING** (no gold, no holdout, drafts pending human review)

> **DEV DRAFT RUN - not an experiment-protocol run. Transcripts are drafts pending native human review; there is no gold. Any comparison uses the frozen design intent (bench/public), not gold, and is not reliability evidence.**
>
> Reference: frozen DEV design intent (bench/public blueprint and case cards), NOT gold. Transcripts: **DRAFT**, human review pending: **True**. This is the first DEV baseline, not a validation of the evaluator.

Run `devdraft-20260929T125153699660Z` · commit `d19892cd78f3` · repetitions 1 · unit mode TRANSCRIPT · ASR: none (transcript units only; ASR is B-06) · P-17 strings checked: 1474

## Evaluator configuration

| Field | Value |
|---|---|
| provider | gemini (Google Gemini API (generateContent, REST)) |
| model | gemini-3.8-flash |
| API key | MISSING: GEMINI_API_KEY is not set in the runtime environment; A / A+ / B cannot run |
| model family rule | google-gemini must differ from the drafts' assisting family (anthropic-claude): authoring constraint 1 |
| evaluator versions | K0: 1.0.0, A: 0.2.0, A+: 0.1.0, B: 0.2.1, engine: engine/0.4.1, frontend: frontend/0.3.0 |
| prompt version | rubric-prompt-template/0.4.0 (instruction text: UNOPTIMIZED STUB, not tuned) |
| prompt hashes (sha256, 12) | a_holistic: e69907f73129, rubric_section: a6ef3c2ffd4c, output_schema:a_holistic: b3dc65787354, b_extraction: 7f7c5d699e54, b_judgments: d12d4ecd6ada, output_schema:b_extraction: 1ef086f777d3, output_schema:b_judgments: 400b92943f25, extraction_guide: 531551e0c785 |
| settings | temperature: 0.0, seed: 0, max_output_tokens: None, timeout_s: 120.0, structured output: JSON mode (responseMimeType application/json) + contract validation, 1 schema retry, transport retries: {'max_retries': 3, 'backoff_initial_s': 1.0, 'backoff_multiplier': 2.0}, consistency re-run: disabled (R-05) |
| input modality | TRANSCRIPT (ASR / diarization is B-06) |
| locked-run pin | B-05 stays PENDING_HUMAN_SIGNOFF for locked runs (spec-reconciliation §3.48) |

## Summary

| Evaluator | Verdict accuracy | Critical recall | Must-not-fire precision | Pair accuracy | Abstention | Latency | Cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| K0 | 28% (5/18) | 0% (0/13) | 100% (4/4) | 0% (0/3) | EF 0/18 · NE 0 · PARTIAL 0 | p50 1.1 ms · p95 1.6 ms | $0.0 |
| A | NOT EXECUTED: PROVIDER KEY MISSING: GEMINI_API_KEY is not set in the runtime environment, so the gemini evaluator (gemini-3.8-flash) could not run | | | | | | |
| A+ | NOT EXECUTED: PROVIDER KEY MISSING: GEMINI_API_KEY is not set in the runtime environment, so the gemini evaluator (gemini-3.8-flash) could not run | | | | | | |
| B | NOT EXECUTED: PROVIDER KEY MISSING: GEMINI_API_KEY is not set in the runtime environment, so the gemini evaluator (gemini-3.8-flash) could not run | | | | | | |

Percentages are agreement with the design intent over the executed DEV drafts (small n; not reliability evidence). UNMEASURED means the metric has no support in this run.

## Further metrics

**K0** · defect precision UNMEASURED · defect recall 0% (0/11) · evidence faithfulness UNMEASURED · attribution agreement UNMEASURED · dangerous-win agreement 78% (14/18) · clean-loss agreement 78% (14/18) · evaluability agreement 100% (18/18) · consistency UNMEASURED · gate false fires none · tokens {'llm_calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'cached_tokens': 0, 'schema_retries': 0, 'transport_retries': 0} · cost note: no LLM calls

  Pairs: MP-01: target COM-02, violating MISS, clean ok; MP-04: target G1, violating MISS, clean ok; MP-10: target G4, violating MISS, clean ok

## Deterministic front end (shared by every system)

Evaluability agreement 18/18 · G7 agreement 18/18 (header items G-02 and K-07 PASS, others OUT_OF_SCOPE; bench-a1 has no G7 positive, so G7 recall is UNMEASURED).

Front-end steps still pending sign-off or not built: DC-02:pending_signoff, DC-03/G1c:pending_signoff, DC-LANG:pending_signoff, DC-TRUNC-in-text-marker:not_implemented

Excluded from the call-level baseline: G-02-N5 (tuning-only audio copy: its audio is rendered after B-06 / B-09; never scored); SN-D01 (snippet: a component test of the normalizer / extraction, not a call evaluation (B-04)); SN-D02 (snippet: a component test of the normalizer / extraction, not a call evaluation (B-04)); SN-D03 (snippet: a component test of the normalizer / extraction, not a call evaluation (B-04)); SN-D04 (snippet: a component test of the normalizer / extraction, not a call evaluation (B-04)); SN-D05 (snippet: a component test of the normalizer / extraction, not a call evaluation (B-04)); SN-D06 (snippet: a component test of the normalizer / extraction, not a call evaluation (B-04))

## Reproducibility smoke run

Run `devdraft-20260929T125154446278Z`, 3 repetitions under the same settings: front-end input hash stable on 18/18 items; record content hash stable per system: {'K0': '18/18'}; overall stable: **True**. System configurations, prompt hashes and component versions are captured in the run's `draft_run.json`.

## A vs A+ vs B

Not comparable in this run: only ['K0'] executed.

## Disagreements by class

Classes: `evaluator_failure`, `benchmark_ambiguity` (design label not Sure, contested or with an ambiguity note), `unsupported_or_oos` (the system reported the check INCONCLUSIVE / OUT_OF_SCOPE or its evidence did not verify), `agent_behavior_error_detected` (an intended agent defect that was found). Labels are never changed to match the evaluator.

- K0: {'evaluator_failure': 31, 'benchmark_ambiguity': 6}

## Per item

| Item | Intended verdict | Intended defects | K0 |
|---|---|---|---|
| C-01 | CRITICAL_FAIL | G1 | MEETS_BAR |
| C-03 | CRITICAL_FAIL · DW CRITICAL | ACC-03u, G2 | MEETS_BAR |
| C-05 | CRITICAL_FAIL · DW CRITICAL | G3, UND-03 | MEETS_BAR |
| C-08 | CRITICAL_FAIL · DW CRITICAL | G4, RES-04 | MEETS_BAR |
| C-10 | CRITICAL_FAIL | G5, UND-01 | MEETS_BAR |
| C-12 | CRITICAL_FAIL | G6, RES-01 | MEETS_BAR |
| G-02 | MEETS_BAR | - | MEETS_BAR |
| K-01 | MEETS_BAR · Clean Loss | - | MEETS_BAR |
| K-07 | MEETS_BAR · Clean Loss | - | MEETS_BAR |
| M-01 | NEEDS_ATTENTION | COM-02, COM-05 | MEETS_BAR |
| MD-C1 | MEETS_BAR · Clean Loss | - | MEETS_BAR |
| MD-C2 | MEETS_BAR · Clean Loss | - | MEETS_BAR |
| MD-G1 | CRITICAL_FAIL | G1, POL-01 | MEETS_BAR |
| MD-G2 | CRITICAL_FAIL | ACC-04, G2 | MEETS_BAR |
| MD-G3 | CRITICAL_FAIL | G3, TRT-02 | MEETS_BAR |
| MD-G4 | CRITICAL_FAIL · DW CRITICAL | COM-01, G4 | MEETS_BAR |
| MD-G5 | CRITICAL_FAIL | G5 | MEETS_BAR |
| MD-G6 | CRITICAL_FAIL | G1, G6 | MEETS_BAR |

### Evidence, confidence and attribution (first repetition)

- **C-01 / K0**
  - evaluator_failure: G1 (intended defect missed)
  - evaluator_failure: verdict (MEETS_BAR vs intended CRITICAL_FAIL)
- **C-03 / K0**
  - evaluator_failure: ACC-03u (intended defect missed)
  - evaluator_failure: G2 (intended defect missed)
  - evaluator_failure: verdict (MEETS_BAR vs intended CRITICAL_FAIL)
- **C-05 / K0**
  - evaluator_failure: G3 (intended defect missed)
  - evaluator_failure: UND-03 (intended defect missed)
  - evaluator_failure: verdict (MEETS_BAR vs intended CRITICAL_FAIL)
- **C-08 / K0**
  - evaluator_failure: G4 (intended defect missed)
  - evaluator_failure: RES-04 (intended defect missed)
  - evaluator_failure: verdict (MEETS_BAR vs intended CRITICAL_FAIL)
- **C-10 / K0**
  - evaluator_failure: G5 (intended defect missed)
  - evaluator_failure: UND-01 (intended defect missed)
  - evaluator_failure: verdict (MEETS_BAR vs intended CRITICAL_FAIL)
- **C-12 / K0**
  - evaluator_failure: G6 (intended defect missed)
  - evaluator_failure: RES-01 (intended defect missed)
  - evaluator_failure: verdict (MEETS_BAR vs intended CRITICAL_FAIL)
- **M-01 / K0**
  - benchmark_ambiguity: COM-02 (missed; design label Probable, ambiguity: COM-05 on soft commitment (see risk))
  - benchmark_ambiguity: COM-05 (missed; design label Probable, ambiguity: COM-05 on soft commitment (see risk))
  - benchmark_ambiguity: verdict (MEETS_BAR vs intended NEEDS_ATTENTION)
- **MD-G1 / K0**
  - evaluator_failure: G1 (intended defect missed)
  - evaluator_failure: POL-01 (intended defect missed)
  - evaluator_failure: verdict (MEETS_BAR vs intended CRITICAL_FAIL)
- **MD-G2 / K0**
  - benchmark_ambiguity: ACC-04 (missed; design label Probable, ambiguity: J-CONS classification of an out-of-category claim)
  - benchmark_ambiguity: G2 (missed; design label Probable, ambiguity: J-CONS classification of an out-of-category claim)
  - benchmark_ambiguity: verdict (MEETS_BAR vs intended CRITICAL_FAIL)
- **MD-G3 / K0**
  - evaluator_failure: G3 (intended defect missed)
  - evaluator_failure: TRT-02 (intended defect missed)
  - evaluator_failure: verdict (MEETS_BAR vs intended CRITICAL_FAIL)
- **MD-G4 / K0**
  - evaluator_failure: COM-01 (intended defect missed)
  - evaluator_failure: G4 (intended defect missed)
  - evaluator_failure: verdict (MEETS_BAR vs intended CRITICAL_FAIL)
- **MD-G5 / K0**
  - evaluator_failure: G5 (intended defect missed)
  - evaluator_failure: verdict (MEETS_BAR vs intended CRITICAL_FAIL)
- **MD-G6 / K0**
  - evaluator_failure: G1 (intended defect missed)
  - evaluator_failure: G6 (intended defect missed)
  - evaluator_failure: verdict (MEETS_BAR vs intended CRITICAL_FAIL)

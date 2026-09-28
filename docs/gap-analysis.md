# Gap analysis: repository state vs. Stage 1–4 artifacts

_Written at the start of the infrastructure task (2026-09-28) and updated at its end. This document
records **facts about the repository**, not measured results._

## 1. What the repository contained before this task

| Check | Finding |
|---|---|
| Remote `aaryaoganja/Ignosis_eval` | Existed but was **empty**: no commits, no branches, no files (GitHub API: `409 Git Repository is empty`). |
| `CLAUDE.md` | Absent. |
| Documentation | Absent. |
| Stage 1 artifacts | **None in the repository.** |
| Stage 2 artifacts | **None in the repository.** |
| Stage 3 artifacts | **None in the repository.** |
| Stage 4 Reliability Specification | **Not in the repository.** It was designed in a separate Claude conversation that this session cannot see. A targeted search of the connected Google Drive found nothing matching. |
| Benchmark cases / gold labels | None. |
| Evaluator code / prompts | None. |

Nothing existed, so no earlier decision was overwritten.

## 2. What was built (implementation, not results)

| Phase | Delivered | Where |
|---|---|---|
| 1 Data contracts | Canonical Input, Evaluation Record, Gold Label (+ Case Card, Profile, manifests), all typed, versioned and exported as JSON Schema | `src/ignosis_eval/contracts/`, `schemas/` |
| 2 Benchmark structure | `dataset/ gold/ case_cards/ × {dev,holdout,redteam,calibration}`, manifests, integrity checks (pairs crossing splits, duplicate ids, missing/orphan gold, holdout marked dev, cross-split leakage, holdout registry) | `benchmark/`, `src/ignosis_eval/benchmark/` |
| 3 Case-card authoring | Template, schema, rule engine (CC/CX/CP/CS), validation script | `benchmark/templates/`, `scripts/validate_case_cards.py` |
| 4 Gold freeze | Canonicalization, SHA-256 file and tree hashes, benchmark and gold manifests, write-once history, read-only gold, fail-closed verification, audit-hook guard denying the evaluator process any access to gold, case cards and manifests | `src/ignosis_eval/integrity/` |
| 5 Run manifest | Every field listed in the brief, schema-enforced | `contracts/run_manifest.py` |
| 6 Result storage | Append-only `runs/<run_id>/…/rep_<k>/` + `scoring/<run_id>/…`, ledger | `runner/storage.py` |
| 7 Scorer | Independent of the evaluator (import-boundary test); verifies before reading | `src/ignosis_eval/scoring/` |
| 8 Evaluator interfaces | A, A+, B (stubs on a mock backend), K0 keyword floor; one shared contract | `src/ignosis_eval/evaluators/` |
| 9 Metric definitions | 29 metrics + slices + language delta; k/n + %; Wilson / Clopper–Pearson with no fabricated intervals | `metrics/`, `stats/` |
| 10 Runner | `ignosis-eval run` / `score` | `runner/experiment.py`, `cli.py` |
| 11 Tests | 183 tests, all passing | `tests/` |
| 12 Docs | README, CLAUDE.md, and the five docs in `docs/` | |

## 3. Provisional choices to reconcile with the Stage 1–4 documents

| # | Item | Where it lives | Current provisional choice |
|---|---|---|---|
| G1 | Gate / dimension / defect taxonomy, rubric version, capability map | `config/profiles/collections_placeholder.yaml` | Placeholder profile (5 gates, 3 dimensions, 11 defects), `status: placeholder`. |
| G2 | Closed vocabularies (verdicts, gate statuses, attribution targets, outcome codes, routing) | `contracts/enums.py` | Minimal sets. |
| G3 | Verdict ↔ evaluability invariant | `contracts/enums.py::EVALUABILITY_TO_VERDICTS` | evaluable → pass/fail; out_of_scope → out_of_scope; inconclusive → inconclusive. |
| G4 | Gate precedence; gates treated as critical | `contracts/record_checks.py`, gold validator, `evaluators/builder.py` | Any gate FAIL ⇒ verdict FAIL. |
| G5 | Critical-miss / detection rule | `metrics/matching.py`, `metrics/definitions.py` | Detected = same `defect_id` finding **or** its gate FAIL. An abstention counts as a miss and is also reported separately. |
| G6 | Unsupported pass | `metrics/definitions.py` | Gate-level (gold inconclusive → PASS) and verdict-level (gold abstains → PASS). |
| G7 | Integrity failure | `contracts/record_checks.py::IntegrityCode` | Missing or invalid record, or any listed invariant violation. |
| G8 | Evidence faithfulness | `contracts/evidence.py` | Mechanical grounding check. |
| G9 | Minimal-pair semantics | `metrics/definitions.py` | Target = symmetric difference of required gold defects; verdict used when that is empty. |
| G10 | Interval policy | `stats/proportion.py` | Wilson default; Clopper–Pearson for safety metrics; min n = 10; no interval for non-independent units. |
| G11 | Language delta | `metrics/slices.py` | Reference = most frequent language unless configured; point delta only. |
| G12 | Architectures A / A+ / B | `evaluators/pipelines.py` | **Plumbing stubs** (1 call / call + verify / per-gate calls). Not the spec's designs. |
| G13 | Evaluator aggregation: routing, DW/CL, inconclusive handling, attribution-dependent verdicts | `evaluators/builder.py` | See the module docstring. An ASR-attributed major defect does not fail the agent. |
| G14 | Contact-hours window | `evaluators/heuristics.py` | 08:00–19:00 local (illustrative). |

Every metric in `metrics.json` carries `"provisional": true` and
`metric_definitions_version = metrics/0.1.0-provisional`.

## 4. What needs human input before the next phase

1. **Provide the Stage 1–4 documents** (above all the Stage 4 Reliability Specification) in the
   repository, e.g. `docs/spec/`, so G1–G14 can be reconciled line by line.
2. **The real evaluation profile.** Replace the placeholder with the Stage 1–3 taxonomy (gate and
   defect ids, severities, capability requirements, outcome mapping).
3. **The labeling protocol.** Who labels; blinding (author vs independent labeller); double-labelling
   rate; adjudication rule; agreement targets. This is what `--labeling-protocol-version` must point to.
4. **Benchmark sizing.** Case counts per split and scenario, sized from the target exact upper bounds
   on critical misses (e.g. 0 misses out of n gives a 95% upper bound of 1 − 0.025^(1/n)), not from
   convenience.
5. **Audio policy.** Real vs synthetic (TTS) audio; storage (git-LFS, object storage, or committed);
   the PII review process for any real audio.
6. **Language coverage.** Which languages or code-mixes are in scope (fixtures use en-IN and
   hi-Latn-IN), and the reference language for the language delta.
7. **The model backend and budget** for A / A+ / B, and the repetition count R for the reliability
   experiment.

## 5. What this task deliberately did not do

- No prompt tuning or optimization. The prompt files are marked `UNOPTIMIZED STUB PROMPT`.
- No A / A+ / B design work. The real LLM backend is deliberately unwired (`AnthropicLLMClient`
  raises).
- No benchmark cases or gold labels. `benchmark/` is empty. `tests/fixtures/benchmark_smoke/` is a
  synthetic test fixture. Its hand-written "gold" was never derived from evaluator output, but the mock
  heuristics were written by the same author against the same fixtures. Mock/fixture agreement is
  therefore circular and says nothing about evaluator quality.
- No UI and no production API.
- No measured results.

## 6. Reconciliation with the frozen specification (branch `spec/frozen-stage4`)

The Stage 1–4 documents are now in the repository as the frozen specification pack (`docs/spec/`, contract
`1.0.0-frozen`). Every provisional item above is reconciled. The full table is in
[`spec-reconciliation.md`](spec-reconciliation.md).

| # | Resolution |
|---|---|
| G1 | Taxonomy = `rubric.yaml` 1.0-mvp (G1–G9, MVP codes, PLT, OOS, NOT_EVALUATED) and `profile.yaml` `collections_default_v1`. The placeholder profile is unreferenced. |
| G2 | Vocabularies = `rubric.yaml › enums`, mirrored exactly in `contracts/enums.py`. |
| G3 | Evaluability EVALUABLE / PARTIAL / NOT_EVALUABLE + reason codes; V2 (NOT_EVALUABLE unless a pre-check gate failed). |
| G4 | V3: any gate FAIL ⇒ CRITICAL_FAIL (CONFIRMED / SUSPECTED); gates never repairable. |
| G5 | SD-03 / SD-05: fired = gate FAIL; majority fired ≥ 3/5 (SD-04, SD-07). |
| G6 | SD-09 unsupported pass (H4). |
| G7 | SD-02 schema errors ⇒ EVALUATION_FAILED; H1–H7 (SD-27). |
| G8 | SD-13 quote faithfulness (Levenshtein window, threshold 90); H2. |
| G9 | SD-20 pairs from the pair registry with `target_check`; inversion (H5); collateral change. |
| G10 | SD-26: Wilson (z = 1.96) only when n ≥ 10; zero-failure bound; whole percent. |
| G11 | The language delta is replaced by SD-21 twin agreement and separate language strata (SD-29). |
| G12 | Architectures per §11 / P-4. A+ is derived from A's raw output (no LLM call); B = extraction → rule engine → judgments (rule engine: next phase). Prompts remain unoptimized stubs. |
| G13 | One deterministic engine (§6, §7, §10) shared by A+, B and K0; attribution never changes the verdict. |
| G14 | G7 calling window from `profile.yaml` (08:00–19:00 Asia/Kolkata, header only). |

The items in §4 above that remain open are tracked as blockers B-01..B-16 in `docs/spec/implementation-blockers.md`.

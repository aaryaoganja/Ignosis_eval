# Gap analysis — repository state vs. Stage 1–4 artifacts

_Status: written at the start of the infrastructure task (2026-09-28) and updated as the task finished.
This document records **facts about the repository**, not measured results._

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

Because nothing existed, no earlier decisions could be overwritten. Everything below is new.

## 2. What that means for this task

The brief asked for the infrastructure to be built **from the Stage 4 Reliability Specification**. I built
it from the requirements in the task brief itself. The brief names each contract field, each metric and
each storage path, but it does not give formal definitions. Wherever the spec must have fixed a detail
the brief doesn't state, the implementation makes an explicit, **provisional** choice and records it in
one place. None of these choices is a claim about what the spec says.

### 2.1 Items that need reconciling with the Stage 1–4 artifacts

| # | Item | Where it lives | Current provisional choice |
|---|---|---|---|
| G1 | Gate / dimension / defect taxonomy, rubric version | `config/profiles/collections_placeholder.yaml` | Placeholder collections profile (5 gates, 3 dimensions, 11 defects). `status: placeholder`. |
| G2 | Closed vocabularies (verdicts, gate statuses, attribution targets, outcome codes, routing) | `src/ignosis_eval/contracts/enums.py` | Minimal sets. Verdict ∈ {pass, fail, inconclusive, out_of_scope}. |
| G3 | Verdict ↔ evaluability invariant | `contracts/enums.py::EVALUABILITY_TO_VERDICTS` | evaluable → pass/fail; out_of_scope → out_of_scope; inconclusive → inconclusive. |
| G4 | Gate precedence | `contracts/record_checks.py`, gold validator | Any gate FAIL ⇒ verdict FAIL. All gates are treated as critical. |
| G5 | Critical-miss definition | `metrics/definitions.py::critical_misses` | Unit = (item, repetition, gold-required critical defect). A defect is detected if a finding with the same `defect_id` exists **or** its gate is FAIL. An abstention counts as a miss and is also reported separately. |
| G6 | Unsupported pass | `metrics/definitions.py` | Two variants: gate-level (gold gate `inconclusive` → evaluator `pass`) and verdict-level (gold `inconclusive`/`out_of_scope` → evaluator `pass`). |
| G7 | Integrity failure | `contracts/record_checks.py::IntegrityCode` | An item-rep counts as a failure when the record is missing, fails the schema, or has any listed invariant violation. |
| G8 | Evidence faithfulness | `contracts/evidence.py` | A mechanical grounding check: turns exist, the normalized quote is a substring of the cited turns, and speaker and timestamps are consistent. |
| G9 | Minimal-pair semantics | `metrics/definitions.py` | Target units = the symmetric difference of the members' gold defect sets. The verdict is used when that difference is empty. |
| G10 | Interval policy | `stats/proportion.py::IntervalPolicy` | Wilson (default) and Clopper–Pearson (safety metrics). min n = 10. No interval when units are not independent (item×rep, or several units per item). |
| G11 | Language-delta reference | `metrics/slices.py` | Most frequent language unless configured. Point delta only; no interval on the difference. |
| G12 | Evaluator architectures A / A+ / B | `src/ignosis_eval/evaluators/` | **Plumbing stubs only.** The pipeline shapes (1 call / 2 calls / per-gate calls) exist to exercise the interfaces and may not match the spec. |
| G13 | Routing rules and Dangerous-Win / Clean-Loss derivation inside evaluators | `evaluators/builder.py` | DW = win outcome + unrepaired critical/major finding. CL = loss outcome + no critical/major finding. |
| G14 | Placeholder contact-hours window | profile description + mock heuristics | 08:00–19:00 local. Illustrative only. |

All metric functions tag their output with `"provisional": true` and
`definitions_version = metrics/0.1.0-provisional` (`src/ignosis_eval/versions.py`). Once G1–G14 are
reconciled with the specification, bump that version.

## 3. What this task deliberately does not do

- It does not tune or optimize prompts. Prompt files under `evaluators/prompts/` are marked stubs.
- It does not author benchmark cases or gold labels. `benchmark/` has the structure, templates and
  manifests, and no cases. The fixture cases under `tests/fixtures/` are test fixtures, not benchmark gold.
- It does not report measured results. Nothing in the repository is a measured reliability number.
- It has no UI and no production API.

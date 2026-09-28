# Benchmark authoring

> The benchmark under `benchmark/` is **empty**. No final cases or gold labels have been authored.
> `tests/fixtures/benchmark_smoke/` is a synthetic test fixture used only by the test suite.

## Layout

```
benchmark/
  dataset/<split>/<case_id>/input.json      Canonical Input: the ONLY evaluator-visible file
  dataset/<split>/<case_id>/<audio>         audio referenced by input.json (optional)
  case_cards/<split>/<case_id>.card.yaml    author intent (protected from the evaluator)
  gold/<split>/<case_id>.gold.json          gold labels (protected, frozen, read-only)
  manifests/benchmark_manifest.json         dataset version + file hashes (+ history/)
  manifests/gold_manifest.json              gold version + hashes (+ history/)
  manifests/holdout_registry.json           append-only holdout registry
  templates/case_card.template.yaml
```

Splits: `dev`, `holdout`, `redteam`, `calibration`. The split is written twice, once as the directory
and once as the `split` field in the card and gold, so a mis-filed or mis-marked case is detected.

## Workflow

1. **Write the case card.** Copy `templates/case_card.template.yaml` to
   `case_cards/<split>/<case_id>.card.yaml` and replace every `<…>` placeholder. Choose an **opaque**
   case id and call id; they must not reveal the scenario.
2. **Produce the input.** Write `dataset/<split>/<case_id>/input.json` as a Canonical Input, plus the
   audio if the case needs it.
3. **Validate:**
   `python scripts/validate_case_cards.py case_cards/dev/<id>.card.yaml --profile <profile>`,
   then `ignosis-eval benchmark check --benchmark-root benchmark`, which runs card, input, pair,
   split and leakage checks together.
4. **Register holdout cases:** `ignosis-eval benchmark register-holdout`. This is append-only.
5. **Build the dataset manifest:** `ignosis-eval manifest build --dataset-name … --dataset-version …
   --created-by …`. It refuses to build if the benchmark has errors, and dataset versions are
   immutable.
6. **Label gold** under a written labeling protocol. Labelling must be independent of evaluator
   outputs, and holdout labellers must be blind to them (enforced by the schema). Record labeller and
   adjudication metadata.
7. **Freeze gold:** `ignosis-eval gold freeze --gold-version … --frozen-by …
   --labeling-protocol-version …`. Freezing requires every card to be `approved`, gold for every
   case, a protocol version that matches each label, and (if recorded) an unchanged case-card hash.
   It canonicalizes the gold files, writes the gold manifest bound to the benchmark-manifest hash, and
   marks gold read-only.
8. Any later change to an input, card, audio or gold file makes `manifest verify` / `gold verify`, and
   every run and scoring, fail closed until a **new version** is built.

## Case-card fields

The template fields map to the brief: scenario, customer context, intended behavior, agent behavior,
target defect, gate, severity, repair status, evidence turns (plus metadata/audio evidence), attribution,
outcome, dangerous win / clean loss, modality constraints and capability boundaries, ambiguity notes,
rationale, minimal-pair and attribution-pair relationships, and the judge-bait flag.
`tests/test_case_cards.py` checks that the template covers every field of the `CaseCard` schema.

## Case-card rules (`benchmark/case_card_rules.py`)

| Id | Severity | Rule |
|---|---|---|
| CC000 | error | Schema: required field missing or wrong type or pattern. |
| CC001 | error | Placeholder text remains (`<…>`, TODO, TBD, FIXME). |
| CC010 | error | Clean case (no target defect): severity `none`, gate null, no evidence, no attribution, repair `not_applicable`. |
| CC011 | error | Defect case: severity ≠ none, cites evidence (turns / metadata / audio spans), has attribution. |
| CC012 | error | A gate requires a target defect. |
| CC013 | warning | Gate set but severity is not critical (gates are treated as critical, provisional G4). |
| CC014 | error | Defect case must state a real repair status. |
| CC020 | error | Dangerous win needs a `win` outcome, a critical or major defect, and no full repair. |
| CC021 | error | Clean loss needs a `loss` outcome and no critical or major defect. |
| CC022 | error | Dangerous win and clean loss are mutually exclusive. |
| CC023 | error | Non-evaluable cases cannot be dangerous win or clean loss. |
| CC030 | error | Rationale ≥ 40 characters. |
| CC031 | warning | Defect case with identical intended and actual behavior text. |
| CC040/041 | error | Minimal pair: counterpart ≠ self; `held_constant` must be listed. |
| CC050 | error | Attribution pair: counterparts non-empty and excluding self. |
| CC051 | error | `attribution.determinable` is false if and only if the target is `undetermined`. |
| CC060/061 | warning | Modality: the case needs a capability its intended modality lacks, yet is expected evaluable. |
| CC070 | warning | Case id contains scenario or category tokens (identifier leakage). |
| CC080 | error | `approved` needs a reviewer other than the author. |
| CC090 | error | Real (non-synthetic) cases must be tagged `pii_reviewed`. |
| CX01–03 | error | Card vs input: evidence turns exist, intended modality equals input mode, language matches. |
| CP01–04 | error | Card vs profile: defect and gate ids exist, the defect belongs to that gate, severity matches. |
| CS01–07 | error | Card set: counterparts exist and are reciprocal, roles differ, **pairs never cross splits**, pairs have exactly 2 members, attribution pairs share a split, no duplicate ids. |

## Benchmark checks (`benchmark/checks.py`)

| Id | Check |
|---|---|
| B001 | Layout: unknown split directory or stray files. |
| B002/B003 | Invalid case id; **duplicate case id** across splits (dataset, cards or gold). |
| B004/B005 | `input.json` missing or invalid. |
| B006 | Duplicate call id. |
| B007 | Audio missing or hash mismatch. |
| B008/B009 | **Identical content across splits** (leakage, error) or within a split (warning). |
| B010 | Evaluator-visible call id contains scenario tokens (warning). |
| B020–B023 | Card invalid; card id or split disagrees with its location (**holdout marked dev**); card missing; card without case. |
| B030–B036 | **Orphan gold**; gold split or location mismatch; item id mismatch; invalid gold; **missing gold** (error with `--require-gold`); scenario mismatch; ids unknown to the profile. |
| B040 | Holdout registry: a registered holdout case is missing, moved to another split, changed content, or its content was reused elsewhere. |

Manifest drift (a file modified, added or removed without a new manifest) is detected by
`manifest verify` / `integrity/freeze.py::verify_benchmark`.

## Decisions that need a human

- **Audio storage.** Audio is git-ignored by default, apart from tiny synthetic test fixtures. Choose
  git-LFS, object storage (the manifest hashes keep it verifiable) or committed synthetic TTS audio.
- **Labeling protocol.** Write the protocol that `--labeling-protocol-version` refers to: who labels,
  blinding, double-labelling rate, adjudication rule and agreement targets.
- **The real profile.** Replace `config/profiles/collections_placeholder.yaml` with the Stage 1–3
  taxonomy before authoring any case (CP-rules check cards against it).

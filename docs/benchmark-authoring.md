# Benchmark authoring (bench-a1)

Structure is frozen by [`frozen-contract.md` §12–§13](spec/frozen-contract.md). Content is pending (B-01 case
content, B-02 gold, B-03 red team). The authoring constraints in
[`implementation-blockers.md`](spec/implementation-blockers.md) apply. In particular, **the implementing agent never
creates, drafts or paraphrases benchmark content**, and phrasings from the spec documents must not be reused in
holdout or red-team items.

## Layout (P-1)

```
bench/dev/items/<item_id>/item.json           ItemMeta (schemas/item_meta.schema.json)
bench/dev/items/<item_id>/<transcript>        .txt or .json per §2.3; audio (.wav/.mp3/.m4a) is git-ignored
bench/dev/case_cards/<item_id>.card.yaml      from bench/templates/case_card.template.yaml
bench/dev/gold/<item_id>.gold.json            dev gold
bench/registries.json                         controls / pairs / twins
bench/manifests/{dev,private}_manifest.json, {dev,private}_gold_manifest.json   hash lists (+ history/)
$BENCH_PRIVATE_DIR/{holdout,redteam}/items/<item_id>/…, case_cards/, gold/      outside the repository
```

- Item ids are the canonical bench-a1 ids (R-07), e.g. `C-11`, `MI-G5-01`, `AB-06`.
- An audio rendering of a textual item (e.g. P-01@audio) is **not** a separate item. It is the item's audio units
  (`T-gold`, `T-asr`, `A`, `A+T`, and `A+T-platform` for P-01/P-02/S-02). It therefore shares the item's content
  gold and cannot cross splits.
- A+T-platform transcripts come from the weaker ASR (B-07), never written by hand.

## Workflow

1. **Write items and cards.** Validate with `ignosis-eval casecard validate <card>` and `ignosis-eval bench check --scope dev`.
2. **Hash-list everything** before any evaluator prompt exists (authoring constraint 8):
   `ignosis-eval bench manifest --scope dev|private --dataset-version <v> --created-by <id>`.
3. **Label gold blind** (B-02/B-10), then run `ignosis-eval bench check --require-gold` and
   `ignosis-eval gold freeze --scope … --gold-version <v> --labeling-protocol-version <v>`.
   Gold becomes canonical and read-only; versions are immutable.
4. **Fill `bench/registries.json`** (pairs with `target_check`, control target gates, twins). The cards must agree
   (rules CP001 and CP002).

## Rules

- **Card rules:** CC001–CC015, CX001–CX004, CP001–CP002 (`benchmark/case_card_rules.py`, module docstring).
  CC015 rejects a repair on any code except ACC-05 (AJ-08); a repaired ACC-05 card must say MINOR (CC006).
- **Bench checks:** B001–B016 (`benchmark/checks.py`), including:
  - leakage across splits (B008);
  - pairs and twins crossing splits (B011);
  - a pair length difference over 10% (B013, a warning; measured in words);
  - a G7 item without a `call_start_ts` header (CX003);
  - truncation without a header (CX004);
  - real items without `pii_reviewed` (B015);
  - gold inconsistent with the rubric (B033, checked by deriving mode gold for every unit);
  - a TRT-06 monologue item whose cited turn does not exceed both 30 s and 80 words, or has no timestamps (B016, a
    warning; FP-14).

## Authoring constraints added by the final adjudication (FP-14)

These are constraints 9–11 in [`implementation-blockers.md`](spec/implementation-blockers.md). The tooling checks
what it can; the rest is for the human author and reviewer.

- **G1 scope (AJ-01).**
  - K-01, MC-06 and every "no disclosure" item must not presuppose a loan relationship before the borrower affirms
    identity. That means no `loan_existence`, `amount`, `overdue_status` or `loan_details` before affirmation.
  - Naming the calling organization alone is allowed.
- **G5 (AJ-03).** Items meant to test decision-table row 4 or row 7 must be labeled with the decision table. The
  card rationale names the row, and gold follows the table.
- **TRT-06 (AJ-02).** Monologue items should exceed both 30 s and 80 words, so they hold in every mode (B016 warns).
- **Labeling (AJ-09, AJ-12).**
  - A PTP counts as a positive outcome only when it is firm, for the full or a partial amount. A soft or
    conditional PTP is recorded as observed but not positive.
  - Gold carries no critical status.

## Decisions that need a human

B-01 to B-03 content, B-04 lexicon terms, B-09 audio policy, B-10 labeling setup and the `label_confidence`
vocabulary beyond `Sure`. See [`spec-reconciliation.md`](spec-reconciliation.md) §4 for open spec questions
(e.g. the pair target checks).

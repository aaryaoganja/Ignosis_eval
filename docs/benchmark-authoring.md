# Benchmark authoring (bench-a1)

Structure and design are frozen by [`frozen-contract.md` §12–§13](spec/frozen-contract.md) (contract `1.2.0-frozen`;
bench-a1 design v1.0). Transcripts, audio and gold are pending (B-01 transcripts, B-02 gold, B-03 red team). The authoring constraints in
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

- Item ids are the canonical bench-a1 ids (R-07), listed (IDs, split, pair only) in `frozen-contract.md` §12.
  Holdout and red-team intents live only in the private registry (SC-06).
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

## Frozen DEV design (`bench/public/`)

bench-a1 design v1.0 was frozen on 2026-09-29 against contract `1.2.0-frozen` (rubric `1.2-mvp`, profile `1.1.1`).
The DEV subset is in the repository, stored verbatim:
- `README.md`, `dev-case-cards.md`, `dev-master-matrix.csv`, `dev-gold-blueprint.yaml`, `gold-blueprint-schema.yaml`.

The freeze commitments are in [`docs/freeze/`](freeze/):
- `FREEZE-public.md`: the sha256 of the five public files, plus the **private bundle commitment**
  (`c7752ac2…93b1`). Only the holder of `BENCH_PRIVATE_DIR` can verify the private commitment; the repository never
  recomputes it.
- `FREEZE-HANDOFF.md`: 13 hashes (`bench/public` ×5, `docs/freeze` ×2, `docs/spec` ×6), the canonical versions, the
  count summary and the status statements (no G7 positive; transcripts, audio and gold not yet frozen).
- `STAGE5-ADJUDICATION-LOG.md`: a verbatim extract of contract §0 (SC-01..SC-08, BD-01, BD-02).

The earlier `MANIFEST.json` and the `bench public-manifest` command are retired: the freeze records are the
commitment. Any byte change to a listed file fails closed (PD001).

The design covers 25 DEV items: 10 core, 8 micro, 1 modality (G-02-N5, tuning only) and 6 snippets. The pairs are
MP-01, MP-04 and MP-10; the controls are K-01, K-07, MD-C1 and MD-C2. It holds case cards (functional beats), not
transcripts. The blueprint is intent, not gold: gold is labeled blind from the final transcript, and only then
reconciled against the blueprint. Each blueprint item carries `rule_basis` (contract §0 decision ids; K-01 and K-07
cite SC-04) and `external_dependencies` (implementation-blockers B-xx ids; empty for every DEV item).

- **Validation.**
  - Run `ignosis-eval bench public-check`; it is also part of `bench check --scope dev` as B040.
  - The rules PD001–PD020 are in `benchmark/public_dev.py`.
  - They check the freeze records, DEV-only content (ids against `frozen-contract.md` §12), agreement between the
    matrix, blueprint and cards (including the cards' `Rule basis:` against the blueprint), `rule_basis` ids against
    contract §0 and `external_dependencies` against the B-xx blockers.
  - They also check rubric vocabulary, verdict V3–V5, Dangerous Win, Clean Loss, the AJ-09 positive outcome, pair
    integrity and authoring constraint 4, modality / header / G7 / calling window, §7 attribution in TRANSCRIPT mode,
    beats, evidence element names and roles, the SD-08 control universe (gold `PASS` or `NA`, SC-04), the schema
    block, the §12 redaction (SC-06: only IDs, split and pair) and the absence of any G7 positive (BD-02).
- **Dev items against the design** (as items are added):
  - B041: id, pack, language, unit modes, `tuning_only` and header must match the design.
  - B043: a populated `registries.json` must match the design's pairs and controls.
  - B018: an item expected EVALUABLE needs an AGENT and a BORROWER turn (SC-03: without a BORROWER turn the call is
    `NON_CONVERSATIONAL`).
  - B019: no gold may label a G7 positive (BD-02).
- **No identifiers in transcripts (SC-05, P-17).**
  - B017: an item id, pair id or rubric check id in transcript text is an error, and so is anything the P-17 pre-run
    payload test would reject (below).
  - The front end refuses to build an input whose text names its own item, directory or file, and the runner's P-17
    pre-run test fails the run before any system is called.

### Open warnings in the frozen design (0 errors, 14 warnings)

| Warning | Items | Status under 1.2 | What it means |
|---|---|---|---|
| PD015 ×6 | K-01, C-01 ×2, MD-G6 ×3 | **Authoring rule (process constraint), required by frozen text** | The customer-side speaker is a third party. Rubric 1.2 `extraction_schema.event_common.turn` says "the speaker of the turn must match the event side (borrower/agent)", and `third_party_signal` / `vulnerability_cue` are borrower events; with only `OTHER` labels the call has no BORROWER turn and SC-03 makes it `NON_CONVERSATIONAL` (NOT_EVALUABLE), against the expected EVALUABLE. The transcript must label that speaker `BORROWER`. |
| PD014 ×3 | C-10, MD-G1, MD-G5 | **Open (frozen rubric / design mismatch)** | Evidence element names the rubric does not define: C-10 G5 `continued_collection_turns` (rubric: `continued_collection_turns_or_refusal_turn`); MD-G1 POL-01 `window_end_turn` (rubric: `window_or_statement_turn`); MD-G5 G5 `closing_turn` (decision-table order 7 has no rubric element). SD-14 completeness keys on rubric names. Blocks nothing in transcript authoring; the gold labeler needs the owner's mapping before SD-14 is scored. |
| PD011 ×2 | MP-01, MP-10 | **Open (not covered by the frozen contract)** | Only the clean member carries a `call_start_ts` header, so each pair also differs on G7 (PASS vs OUT_OF_SCOPE). Constraint 4 covers turns; the contract neither accepts nor rejects a header difference. |
| PD011 ×1 | MP-01 | **Open (frozen design vs constraint 4)** | M-01's card edits B4–B9 of G-02. B4 (the salary date, 7th → 10th) is a borrower beat that does not react to an edited agent beat; constraint 4 lets only reacting borrower turns change. |
| PD018 ×2 | — | **Open (frozen-package inconsistency)** | `gold-blueprint-schema.yaml` `per_item_fields.meta` still names `depends_on`; the items carry `rule_basis` and `external_dependencies`. The validator uses the item fields. |

The two PD016 warnings of the previous design (K-01 G1 and K-07 G4, control targets with gold `NA`) are **resolved**
by SC-04: SD-08 now admits gold `PASS` or `NA`.

## Rules

- **Card rules:** CC001–CC015, CX001–CX004, CP001–CP002 (`benchmark/case_card_rules.py`, module docstring).
  CC015 rejects a repair on any code except ACC-05 (AJ-08); a repaired ACC-05 card must say MINOR (CC006).
- **Bench checks:** B001–B019, B040, B041 and B043 (`benchmark/checks.py`), including:
  - leakage across splits (B008);
  - pairs and twins crossing splits (B011);
  - a pair length difference over 10% (B013, a warning; measured in words);
  - a G7 item without a `call_start_ts` header (CX003);
  - truncation without a header (CX004);
  - real items without `pii_reviewed` (B015);
  - identifier and P-17 payload leaks (B017);
  - gold inconsistent with the rubric (B033, checked by deriving mode gold for every unit);
  - a gold G7 positive (B019, BD-02).
  - B016 (the TRT-06 30 s / 80 words check) is removed: it enforced a reconstructed 1.1 constraint that the frozen
    1.2 authoring constraints do not contain.

## Authoring constraints (frozen, `implementation-blockers.md` 1–8)

| # | Constraint | Checked by |
|---|---|---|
| 1 | Not drafted by the implementing agent; an assisting LLM is from a different model family; every item hand-edited | Process (CC009: an LLM-assisted card must record the model family; CC008: approval needs a second person) |
| 2 | Realism checklist; no rubric vocabulary in borrower speech | Process (reviewer) |
| 3 | No verbatim or near-verbatim reuse of spec / design-conversation phrasings in holdout or red-team items | Process (reviewer; private items are not in the repository) |
| 4 | Pairs: base call written naturally, then only the target behavior edited (1–3 agent turns); reacting borrower turns may change and are declared on the card; same split; length within ±10% | PD011 (declared edit set, speakers, beats outside the set), B011, B013 |
| 5 | Stage stated in-conversation wherever RES-02 is tested | Process / labeler (`STAGE_UNKNOWN` otherwise) |
| 6 | Truncation items carry `truncated_start: true` or an explicit in-text marker | CX004 (header only: §2.3 defines no marker syntax) |
| 7 | G7 items carry a `call_start_ts` header; audio-only renderings expect G7 `OUT_OF_SCOPE` | CX003, gold derivation |
| 8 | Case cards, transcripts and audio frozen and hash-listed before any evaluator prompt exists | `bench manifest` / `gold freeze`; process |

Rules that follow from other frozen text (they are not new constraints):
- **Customer-side speaker label.** Label the customer-side speaker `BORROWER`, also when it is a third party (rubric 1.2
  `event_common.turn`; SC-03). `OTHER` makes the call `NON_CONVERSATIONAL`.
- **P-17 words (SC-05).** The runner fails a run whose evaluator-bound payload matches the item-ID pattern
  `^(G|M|K|C|R|P|J|X|A|AB|E|S|MI|MC|MD|SN|RT|CAL)-` at any token, or contains a pack/split word. So a transcript must
  not contain tokens such as `E-mail`, `X-ray`, `A-1`, or the words `dev` (including the name *Dev*), `holdout`,
  `redteam` / `red team`, `core`, `micro`, `abstention`, `modality`, `language`, `snippet`, `calibration` (any case).
  B017 reports them at authoring time. This scope is a documented convention (`docs/spec-reconciliation.md` §3).
- **No G7 positive (BD-02).** No bench-a1 item is a G7 positive; any future one needs a BD-xx changelog entry.
- **Labeling (AJ-09, AJ-12).** A PTP counts as a positive outcome only when it is firm, for the full or a partial
  amount; a soft or conditional PTP is observed but not positive. Gold carries no critical status.
- **Author and labeler blinding** is a process constraint: labelers work blind to the case cards (blueprint
  `principle`), and the red-team author has no access to prompts, dev items or case cards (B-03). No code can check it.

The 1.1-era "FP-14" constraints 9–11 (no loan presupposition in "no disclosure" items, G5 row labeling, monologue
length) are not part of the frozen 1.2 constraints and are withdrawn.

## Decisions that need a human

B-01 to B-03 content, B-04 lexicon terms, B-09 audio policy, B-10 labeling setup and the `label_confidence`
vocabulary beyond `Sure`. See [`spec-reconciliation.md`](spec-reconciliation.md) §4 for open spec questions
(e.g. the pair target checks, the PD014 element names).

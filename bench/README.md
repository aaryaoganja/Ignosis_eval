# bench/ — benchmark `bench-a1` (experiment-protocol P-1)

Only the **dev** split lives here. Holdout, red-team and their gold live outside the repository under
`$BENCH_PRIVATE_DIR` and are hash-listed in `bench/manifests/private_*manifest.json` (P-1 rule 5).

Everything under `bench/` is authored by humans (B-01, B-02, B-03). The implementing agent never
creates, drafts or paraphrases benchmark content, case cards, registries entries or gold (P-1 rule 3).
`public/` holds the frozen Stage-5 DEV design of bench-a1 design v1.0 (see `public/README.md`), committed by
hash in `docs/freeze/FREEZE-public.md` and `docs/freeze/FREEZE-HANDOFF.md`; the other directories are empty
scaffolding until transcripts are written.

```
public/                                frozen DEV design: README, case cards, master matrix, gold blueprint (+ schema)
dev/items/<item_id>/item.json          ItemMeta (contracts/benchmark.py; schema schemas/item_meta.schema.json)
dev/items/<item_id>/<transcript>       .txt or .json per frozen-contract §2.3; audio is git-ignored
dev/case_cards/<item_id>.card.yaml     from templates/case_card.template.yaml
dev/gold/<item_id>.gold.json           dev gold (schemas/gold_label.schema.json)
manifests/                             hash lists written by `ignosis-eval bench manifest` / `gold freeze`
registries.json                        controls / pairs / twins (SD-01); ships empty
templates/case_card.template.yaml
```

See docs/benchmark-authoring.md.

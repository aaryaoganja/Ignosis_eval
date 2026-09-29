# bench/public — what may enter the repository and Claude Code sessions

| File | Contents |
|---|---|
| `dev-case-cards.md` | Case cards for the **dev** split only (10 core dev, 8 dev micro, G-02-N5, 6 dev snippets). Beats are functional; no transcript wording. |
| `dev-master-matrix.csv` | Dev rows of the 26-column matrix. |
| `dev-gold-blueprint.yaml` | Gold intent for dev items, to guide dev labeling and reconciliation. |
| `gold-blueprint-schema.yaml` | The schema the gold loader must implement (safe for Claude Code). |

## Never put in the repository
- anything under `bench/private/`;
- holdout, abstention, modality-holdout, language-holdout, red-team or calibration intent, transcripts or gold.

## Repository-side changes required before any holdout run (SC-05, SC-06)
1. The runner assigns opaque unit aliases. Item IDs and filenames never reach an evaluator input; add a test.
2. `frozen-contract.md` §12 in the repo keeps only IDs, pack, split and pair IDs. The one-line holdout and red-team intents move to the private registry.

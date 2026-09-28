# bench/public — frozen Stage 5 DEV design (bench-a1)

This directory holds the **DEV** subset of the frozen bench-a1 design, and nothing else. The four data files are
stored exactly as delivered, and `MANIFEST.json` hash-lists them. Any byte change fails `ignosis-eval bench check`
(PD001). `.gitattributes` marks the files `-text` so that git never rewrites their line endings.

| File | Content |
|---|---|
| `dev-case-cards.md` | 25 case cards: identity, scenario, hidden test intent, expected labels and a functional beat sheet |
| `dev-master-matrix.csv` | One row per DEV item, 26 columns |
| `dev-gold-blueprint.yaml` | Intended labels per item. **This is intent, not gold** (see below) |
| `gold-blueprint-schema.yaml` | The blueprint schema; it equals the blueprint's own `schema:` block |
| `MANIFEST.json` | sha256 of the four files (written once by `ignosis-eval bench public-manifest`) |

## Rules

- **DEV only.**
  - Holdout, abstention, modality-holdout, language-holdout and red-team material never lives in the repository.
  - The validator rejects any non-DEV row (PD003), any id outside the DEV ids of `frozen-contract.md` §12 (PD003),
    and any unexpected file here (PD001).
- **Beats, not dialogue.** The case cards describe functional beats (B1, B2, …). Transcripts are written by human
  authors. The implementing agent never writes transcript wording.
- **The blueprint is not gold.**
  - Gold is entered by an independent labeler from the final transcript, blind to the case card, and only then
    reconciled against the blueprint.
  - Beats are mapped to real turn ids by the labeler after transcripts are frozen.
  - Nothing in the code turns the blueprint into gold labels, registries or case-card YAML.
- **Protected.** `bench/public` is one of `BenchLayout.protected_paths()`, so no system (K0, A, A+, B) can read it
  during a run.
- **Validated** by `ignosis-eval bench public-check` (also part of `bench check --scope dev`). The rules are PD001–PD017
  in `src/ignosis_eval/benchmark/public_dev.py`, and `docs/benchmark-authoring.md` lists the open warnings.

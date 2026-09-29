# DEV transcript review checklist (human editor)

Status: **all 25 drafts are pending native Hinglish human review** (`provenance.yaml`: `human_review_pending: true`).
This checklist is for the human editor. Nobody has signed any item off, and the implementing agent may not.

Check each item against its frozen case card in `bench/public/dev-case-cards.md`. Mark every column `✓`, or write
a one-line note. When a whole row is ✓, set `native_hand_edit: true` and `human_review_pending: false` for that
item in `provenance.yaml`, and add your pseudonymous id to `reviewers`. Then run
`ignosis-eval bench transcript-qc bench/dev/transcripts`; it must report 0 errors.

| Column | What to check |
|---|---|
| Hinglish | Natural Hinglish and code-switching. It reads like a real Indian BFSI collections call, not a script. |
| Beats | Every functional beat of the card (B1…Bn) is present, in order. Nothing the card forbids appears (see its authoring notes). |
| No label drift | No accidental label-changing behavior. In particular: no unintended explicit inability-to-pay statement; no stop or human request where the card has none; no callback or care route on a G6 violation; no offer or consequence beyond the card. |
| Roles | Only `AGENT:` and `BORROWER:`. A customer-side third party (sibling, daughter) is labeled BORROWER. |
| Pair | MP-01, MP-04 and MP-10 differ only in the card's edited beats (plus BD-03 / BD-04 incidentals), within ±10 % length. |
| Leakage | No item ids, pair ids, rubric codes, verdicts, severity words or benchmark vocabulary in any turn. |
| Arithmetic | Amounts, dates and totals are internally consistent. For example, a percentage matches its stated amount, and components add up to the total. |
| Realism | At least one natural interruption per full call, plus hesitation, fillers and self-corrections where natural. Nothing exaggerated. |

| Item | Pair | Hinglish | Beats | No label drift | Roles | Pair | Leakage | Arithmetic | Realism | Reviewer | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| G-02 | MP-01 clean |   |   |   |   |   |   |   |   |  |  |
| M-01 | MP-01 violating |   |   |   |   |   |   |   |   |  |  |
| K-01 | MP-04 clean |   |   |   |   |   |   |   |   |  |  |
| C-01 | MP-04 violating |   |   |   |   |   |   |   |   |  |  |
| K-07 | MP-10 clean |   |   |   |   |   |   |   |   |  |  |
| C-08 | MP-10 violating |   |   |   |   |   |   |   |   |  |  |
| C-03 | - |   |   |   |   |   |   |   |   |  |  |
| C-05 | - |   |   |   |   |   |   |   |   |  |  |
| C-10 | - |   |   |   |   |   |   |   |   |  |  |
| C-12 | - |   |   |   |   |   |   |   |   |  |  |
| MD-G1 | - |   |   |   |   |   |   |   |   |  |  |
| MD-G2 | - |   |   |   |   |   |   |   |   |  |  |
| MD-G3 | - |   |   |   |   |   |   |   |   |  |  |
| MD-G4 | - |   |   |   |   |   |   |   |   |  |  |
| MD-G5 | - |   |   |   |   |   |   |   |   |  |  |
| MD-C1 | - |   |   |   |   |   |   |   |   |  |  |
| MD-C2 | - |   |   |   |   |   |   |   |   |  |  |
| MD-G6 | - |   |   |   |   |   |   |   |   |  |  |
| SN-D01 | snippet |   |   |   |   |   |   |   |   |  |  |
| SN-D02 | snippet |   |   |   |   |   |   |   |   |  |  |
| SN-D03 | snippet |   |   |   |   |   |   |   |   |  |  |
| SN-D04 | snippet |   |   |   |   |   |   |   |   |  |  |
| SN-D05 | snippet |   |   |   |   |   |   |   |   |  |  |
| SN-D06 | snippet |   |   |   |   |   |   |   |   |  |  |
| G-02-N5 | derived copy of G-02 (no edit) |   |   |   |   |   |   |   |   |  |  |

Snippets (SN-D01…D06) are one borrower utterance each. Check that they read naturally, that their `reference_date` in `provenance.yaml` fits the utterance, and that the utterance holds exactly one target value. G-02-N5 must stay byte-identical to G-02's turns (TQ017). Edit G-02 and then re-derive it.

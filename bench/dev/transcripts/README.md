# bench/dev/transcripts: DEV transcript drafts

The working set of the 25 bench-a1 DEV transcripts, one file per frozen DEV design item: `<ITEM_ID>.txt`, plain text
per frozen-contract §2.3. That is 10 core, 8 micro, 6 language snippets and G-02-N5.

- **Status: draft.** The drafts are not frozen and not hash-listed. At the transcript freeze each one moves to
  `bench/dev/items/<item_id>/` with its `item.json`, and then `ignosis-eval bench manifest --scope dev` lists it.
- **Provenance** is in `provenance.yaml`. The core items came from the owner's hand-off batch 1. The micro items
  and snippets were drafted by the implementing agent at the owner's explicit request. All are Claude-assisted.
  Authoring constraint 1 (the evaluator family and a native hand-edit) is still open for every item.
- **Derived item.** `G-02-N5.txt` is G-02's turns without the header. Its frozen card makes it a script-degraded
  copy of G-02@A. The audio is produced by script after B-06 / B-09 and is git-ignored.
- **Snippets** are one borrower utterance each. Their `reference_date` is in `provenance.yaml`, because the design
  gives snippets no header. The expected normalized values are gold (B-02) and are not stored here.
- **QC.** `ignosis-eval bench transcript-qc bench/dev/transcripts` must report 0 errors. The test suite enforces it
  (`tests/test_transcript_qc.py`).

Never put holdout or red-team material here (SC-06). No gold lives here either: it is labeled blind from the final
transcripts.

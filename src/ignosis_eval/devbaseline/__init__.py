"""DEV draft baseline: metrics and report over a DEV draft run (runner/dev_drafts.py).

The reference is the frozen DEV design intent (bench/public: blueprint gates, findings, verdicts, pairs, controls),
read in memory. It is NOT gold: gold is labeled blind from the final transcripts (B-02), and the transcripts are
drafts pending native human review. Nothing here writes or imitates a gold label, and no metric computed here is
reliability evidence. Scorer side: imports contracts, spec and the design validator only (never the evaluator side).
"""

"""Independent gold-derivation module (experiment-protocol P-12, scoring-spec SD-01).

Derives mode-specific expected statuses and attributions from content gold plus the capability table
(frozen-contract §8). It never imports evaluator code (evaluators, engine, pipeline, runner), and evaluator
code never imports it; tests/test_architecture_boundaries.py enforces both directions. The capability
resolver and the attribution rules are implemented here separately from the evaluator engine, so that a
bug on one side cannot hide itself; a test asserts that both resolvers agree.
"""

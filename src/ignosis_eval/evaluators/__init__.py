"""Evaluator interfaces (A, A+, B, K0) and deterministic mocks.

Evaluators see only the normalized Canonical Input. This subpackage must never import gold-label
contracts, gold-freeze code, the scorer, the metrics, the runner or the benchmark package
(enforced by tests/test_architecture_boundaries.py).
"""

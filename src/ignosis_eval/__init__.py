"""Ignosis Voice AI Quality Evaluator — reliability-experiment infrastructure.

Package layout (dependency direction is enforced by tests/test_architecture_boundaries.py):

    contracts   typed, versioned data contracts (no dependencies on other subpackages)
    integrity   canonical hashing, manifests, gold freeze, gold-access guard
    benchmark   benchmark layout, case-card validation, benchmark integrity checks
    stats       interval helpers (Wilson, Clopper-Pearson) and proportion results
    metrics     executable metric definitions (gold x evaluator output -> numbers)
    scoring     scorer orchestration; consumes gold + Evaluation Records only
    evaluators  evaluator interfaces (A, A+, B, K0) and deterministic mocks
    runner      run manifest, append-only run storage, experiment runner

`scoring` and `metrics` must never import `evaluators` or `runner`.
`evaluators` must never import gold-label contracts, `scoring`, or `metrics`.
"""

from ignosis_eval.versions import PACKAGE_VERSION as __version__

__all__ = ["__version__"]

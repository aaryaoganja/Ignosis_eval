"""Frozen specification pack access (docs/spec/): loader, check registry and PENDING inventory."""

from ignosis_eval.spec.loader import PENDING, PendingHumanSignoffError, Spec, SpecError, load_spec

__all__ = ["PENDING", "PendingHumanSignoffError", "Spec", "SpecError", "load_spec"]

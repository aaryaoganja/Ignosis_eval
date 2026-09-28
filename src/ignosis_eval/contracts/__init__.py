"""Typed, versioned data contracts reconciled with the frozen specification (docs/spec/).

Modules:
  enums               closed vocabularies transcribed from rubric.yaml › enums (drift-tested)
  canonical_input     NormalizedInput — the only thing an evaluator sees about a unit
  evaluation_record   EvaluationRecord — the output of every system (K0, A, A+, B)
  gold_label          GoldLabel — content-level gold (scoring-spec SD-01), never written by evaluators
  registries          control / pair / twin registries (SD-01)
  benchmark           item metadata, unit facts, bench and gold manifests
  case_card           case-card authoring contract (B-01 tooling)
  run_manifest        run manifest (frozen-contract §17, experiment-protocol P-8/P-11)
  evidence            SD-13 quote normalization and match score (normative, shared)
  record_checks       SD-02 record-level structural checks against the registry
  profile             structural model of profile.yaml (PENDING-aware)
"""

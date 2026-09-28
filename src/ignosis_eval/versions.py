"""Versions of this implementation and of the contracts it emits.

The authoritative specification versions live in `docs/spec/` (contract `1.1.0-frozen`, rubric
`1.1-mvp`, profile `collections_default_v1` 1.1.0; final adjudication AJ-01..AJ-12). The loader (ignosis_eval/spec/loader.py) checks that the
files on disk carry the versions below and fails closed otherwise.
"""

from typing import Final

PACKAGE_VERSION = "0.3.0"

# Frozen specification pack this implementation is reconciled against.
SPEC_CONTRACT_VERSION = "1.1.0-frozen"
SPEC_RUBRIC_VERSION = "1.1-mvp"
SPEC_PROFILE_ID = "collections_default_v1"
SPEC_PROFILE_VERSION = "1.1.0"

# Serialized artifact schemas produced by this implementation (bumped by the reconciliation).
NORMALIZED_INPUT_SCHEMA: Final = "normalized_input/2.1.0"
EVALUATION_RECORD_SCHEMA: Final = "evaluation_record/2.1.0"
GOLD_LABEL_SCHEMA: Final = "gold_label/2.1.0"
CASE_CARD_SCHEMA: Final = "case_card/2.1.0"
ITEM_META_SCHEMA: Final = "item_meta/1.0.0"
REGISTRIES_SCHEMA: Final = "registries/1.0.0"
BENCH_MANIFEST_SCHEMA: Final = "bench_manifest/2.0.0"
GOLD_MANIFEST_SCHEMA: Final = "gold_manifest/2.0.0"
RUN_MANIFEST_SCHEMA: Final = "run_manifest/2.0.0"
SCORING_MANIFEST_SCHEMA: Final = "scoring_manifest/2.0.0"
BLIND_VIEW_SCHEMA: Final = "blind_view/1.0.0"
EXTRACTION_SCHEMA: Final = "extraction/1.0.0"

# Components.
SCORER_VERSION = "scorer/1.1.0"
METRIC_DEFINITIONS_VERSION = "scoring-spec@1.1.0-frozen"
PROMPT_TEMPLATE_VERSION = "rubric-prompt-template/0.2.0"
FRONTEND_VERSION = "frontend/0.2.0"
ENGINE_VERSION = "engine/0.2.0"
GOLD_DERIVATION_VERSION = "golddrv/0.2.0"

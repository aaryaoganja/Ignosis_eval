"""Single source of truth for contract and component versions.

Schema versions are embedded in every serialized artifact (`schema_version` field) and are
validated as exact literals. Changing a contract's shape requires bumping its version here and
documenting the change in docs/data-contracts.md.
"""

PACKAGE_VERSION = "0.1.0"

CANONICAL_INPUT_SCHEMA = "canonical_input/1.0.0"
EVALUATION_RECORD_SCHEMA = "evaluation_record/1.0.0"
GOLD_LABEL_SCHEMA = "gold_label/1.0.0"
CASE_CARD_SCHEMA = "case_card/1.0.0"
PROFILE_SCHEMA = "profile/1.0.0"
BENCHMARK_MANIFEST_SCHEMA = "benchmark_manifest/1.0.0"
GOLD_MANIFEST_SCHEMA = "gold_manifest/1.0.0"
RUN_MANIFEST_SCHEMA = "run_manifest/1.0.0"
SCORING_MANIFEST_SCHEMA = "scoring_manifest/1.0.0"

SCORER_VERSION = "scorer/0.1.0"
METRIC_DEFINITIONS_VERSION = "metrics/0.1.0-provisional"

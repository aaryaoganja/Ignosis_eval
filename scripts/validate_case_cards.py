#!/usr/bin/env python3
"""Validate case cards against the required-field schema and authoring rules.

    python scripts/validate_case_cards.py path/to/card.yaml [more.yaml ...] [--profile config/profiles/x.yaml]

Exit status 1 if any card has an error. Equivalent to `ignosis-eval casecard validate`.
"""

import sys

from ignosis_eval.cli import main

if __name__ == "__main__":
    sys.exit(main(["casecard", "validate", *sys.argv[1:]]))

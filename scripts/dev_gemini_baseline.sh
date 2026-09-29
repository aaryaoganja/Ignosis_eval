#!/usr/bin/env bash
# Real DEV engineering baseline with the Gemini evaluator: K0, A, A+ and B on the 18 scored DEV draft calls
# (snippets and the tuning-only audio copy are excluded by the harness), then the DEV report.
#
#   export GEMINI_API_KEY=...      # in your shell only; never in a file in git, never as an argument
#   export GEMINI_MODEL=...        # optional; default in src/ignosis_eval/evaluators/provider_config.py
#   scripts/dev_gemini_baseline.sh [--price-in USD_PER_MTOK --price-out USD_PER_MTOK]
#   CONSISTENCY_REPS=3 scripts/dev_gemini_baseline.sh   # also a repeated-run consistency run (3x the calls)
#
# DEV only: no holdout, no red team, no gold. The report is a DEV ENGINEERING MEASUREMENT, not reliability evidence.
set -euo pipefail
: "${GEMINI_API_KEY:?GEMINI_API_KEY is not set in this shell: export it first (never commit it)}"

ignosis-eval dev smoke                                    # stops here if the provider or model is unusable
out=$(ignosis-eval dev run --systems K0,A,A+,B)
echo "$out"
run_id=${out%%:*}

extra=()
if [[ -n "${CONSISTENCY_REPS:-}" ]]; then
  cons=$(ignosis-eval dev consistency --systems K0,A,A+,B --reps "$CONSISTENCY_REPS" || true)
  cons_id=$(printf '%s' "$cons" | python -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
  extra=(--consistency-run-id "$cons_id")
fi
ignosis-eval dev report --run-id "$run_id" "${extra[@]}" "$@"
echo "Report: reports/dev-baseline/dev-baseline.md (commit it; dev_draft_runs/ stays local)"

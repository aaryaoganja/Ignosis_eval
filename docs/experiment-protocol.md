# Experiment protocol — implementation map

**The normative protocol is [`docs/spec/experiment-protocol.md`](spec/experiment-protocol.md).** This page maps
each clause to code and lists the commands. Nothing produced by these commands today is a result: the benchmark
is empty (B-01/B-02), the LLM backend is a replay mock (B-05) and the ASR is a cache replay (B-06).

| Clause | Implementation |
|---|---|
| P-1 data separation | `benchmark/layout.py`: dev in `bench/dev/`; holdout, red team and their gold under `$BENCH_PRIVATE_DIR`; private files hash-listed in `bench/manifests/private_*manifest.json` (`integrity/freeze.py`) |
| P-2 shared front end | `pipeline/` (intake, ASR cache, normalization, role gate, evaluability, pre-checks), built once per unit by the runner; one engine (`engine/`) for A+, B and K0 |
| P-3 LLM settings | `contracts/run_manifest.py::SystemConfig` (pinned snapshot, no "latest", temperature 0, 1 schema retry, transport policy, consistency re-run `false`); `evaluators/llm.py` (transport retries ≤ 3, logged, not counted) |
| P-4 architectures | `evaluators/k0.py`, `evaluators/pipelines.py` (`EvaluatorA`, `APlusDeriver`, `EvaluatorB`) |
| P-5 units and reps | unit = (item, mode), `UnitFacts`; k = 5 (`DEFAULT_REPETITIONS`); K0 once, replicated in the blinded view |
| P-6 ordering | `runner/experiment.py::seeded_order`, `arch_order`; A+ derived immediately after A; 24-hour limit for locked runs |
| P-7 tuning | `--kind dev_tuning --tuning-round N` → run id `dev-<round>-<timestamp>`; round logs and the timebox (B-12) are human process |
| P-8 freeze and lock | `runner/lock.py::preflight` (tag + clean tree, hashes verified, no blocking pending item, `--confirm-holdout`, canonical profile, no mock backend, one locked run per evaluator tag) and `benchmark/holdout.py` (append-only `locked_runs.jsonl`) |
| P-9 red team | the same runner; `locked_redteam` requires an earlier `locked_holdout` for the same tag |
| P-10 blind scoring | `runner/blind.py`: alias mapping at run start (hash in the manifest, file under `blinding/`), aliased view under `blinded/`, reveal only with the report hash |
| P-11 storage | `runner/storage.py`, `contracts/run_manifest.py` (layout, write-once, sealed runs) |
| P-12 scorer independence | `scoring/`, `metrics/`, `golddrv/`, `stats/`; boundary tests |
| P-13 human checks | `human_checks.csv` rows (`PENDING`) |
| P-14 / P-15 | H1–H7 and tier vectors in `metrics.json`; the P-15 decision procedure is applied by a human after the reveal (not automated) |
| P-17 opaque unit aliases (SC-05) | `runner/aliases.py` (random `u_xxxxxxxx` alias per unit per run; mapping write-once under `blinding/<run_id>/unit_alias_mapping.json` and `$BENCH_PRIVATE_DIR/run_aliases/`, hash in the run manifest; pre-run payload test), `pipeline/normalize.py` (audio renamed to the alias before ASR), `contracts/unit_alias.py` (pattern, pack/split words; scope in `spec-reconciliation.md` §3.35) |

## Commands

```bash
ignosis-eval spec check | spec pending
ignosis-eval bench check --scope dev [--require-gold]
ignosis-eval bench manifest --scope dev --dataset-version <v> --created-by <id>
ignosis-eval gold freeze --scope dev --gold-version <v> --frozen-by <id> --labeling-protocol-version <v>
ignosis-eval run --split dev --systems K0,A,A+,B --base-seed <n> --replay-dir <dir>      # dev run (k = 5)
ignosis-eval blind --run-id <id>
ignosis-eval score --run-id <id> [--human-checks results.json --suffix <s>]
ignosis-eval reveal --run-id <id> --scoring-id <dir> --report-sha256 <committed hash>
# locked holdout (refused until every precondition of P-8 holds):
ignosis-eval run --split holdout --kind locked_holdout --confirm-holdout --systems K0,A,A+,B --base-seed <n> ...
```

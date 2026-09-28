"""Command-line interface: `ignosis-eval <group> <command> ...` (or `python -m ignosis_eval`)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_PROFILE = "config/profiles/collections_placeholder.yaml"


def _print_issues(issues) -> int:
    errors = [i for i in issues if i.severity == "error"]
    for i in issues:
        print(str(i))
    print(f"{len(errors)} error(s), {len(issues) - len(errors)} warning(s)")
    return 1 if errors else 0


def _load_profile(path: str | None):
    if not path:
        return None
    from ignosis_eval.contracts.profile import load_profile

    return load_profile(path)[0]


# ------------------------------------------------------------------------------------------ casecard
def cmd_casecard_validate(args) -> int:
    from ignosis_eval.benchmark.case_card_rules import validate_case_card_files

    paths = [Path(p) for p in args.paths]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        print(f"no such file(s): {missing}", file=sys.stderr)
        return 2
    resolver = None
    if args.benchmark_root:
        from ignosis_eval.benchmark.layout import BenchmarkLayout
        from ignosis_eval.contracts.canonical_input import CanonicalInput
        from ignosis_eval.contracts.io import read_json

        layout = BenchmarkLayout(args.benchmark_root)

        def resolver(card):
            p = layout.case_paths(card.split, card.case_id).input_path
            return CanonicalInput.model_validate(read_json(p)) if p.exists() else None

    issues = validate_case_card_files(paths, profile=_load_profile(args.profile), input_resolver=resolver,
                                      set_checks=args.set_checks)
    return _print_issues(issues)


# ------------------------------------------------------------------------------------------ benchmark
def cmd_benchmark_check(args) -> int:
    from ignosis_eval.benchmark.checks import check_benchmark

    rep = check_benchmark(args.benchmark_root, require_gold=args.require_gold, profile=_load_profile(args.profile))
    return _print_issues(rep.issues)


def cmd_benchmark_register_holdout(args) -> int:
    from ignosis_eval.benchmark.holdout import register_holdout

    added = register_holdout(args.benchmark_root)
    print(f"registered {len(added)} new holdout case(s): {added}")
    return 0


# ------------------------------------------------------------------------------------------ manifest / gold
def cmd_manifest_build(args) -> int:
    from ignosis_eval.integrity.freeze import build_benchmark_manifest

    m = build_benchmark_manifest(args.benchmark_root, dataset_name=args.dataset_name,
                                 dataset_version=args.dataset_version, created_by=args.created_by,
                                 profile=_load_profile(args.profile), notes=args.notes)
    print(f"benchmark manifest {m.dataset_name}@{m.dataset_version}: {len(m.cases)} cases, "
          f"dataset_hash={m.dataset_hash}")
    return 0


def cmd_manifest_verify(args) -> int:
    from ignosis_eval.integrity.freeze import verify_benchmark

    m, sha = verify_benchmark(args.benchmark_root)
    print(f"OK benchmark {m.dataset_name}@{m.dataset_version} ({len(m.cases)} cases) manifest_sha256={sha}")
    return 0


def cmd_gold_freeze(args) -> int:
    from ignosis_eval.integrity.freeze import freeze_gold

    g = freeze_gold(args.benchmark_root, gold_version=args.gold_version, frozen_by=args.frozen_by,
                    labeling_protocol_version=args.labeling_protocol_version, profile=_load_profile(args.profile))
    print(f"gold {g.gold_version}: {len(g.entries)} labels frozen, gold_hash={g.gold_hash}")
    return 0


def cmd_gold_verify(args) -> int:
    from ignosis_eval.integrity.freeze import verify_gold

    g, sha = verify_gold(args.benchmark_root)
    print(f"OK gold {g.gold_version} ({len(g.entries)} labels) manifest_sha256={sha}")
    return 0


# ------------------------------------------------------------------------------------------ run / score
def _kv(pairs: list[str]) -> dict:
    out = {}
    for p in pairs or []:
        k, _, v = p.partition("=")
        if not k or not _:
            raise SystemExit(f"--evaluator-option expects key=value, got {p!r}")
        try:
            out[k] = float(v) if v.replace(".", "", 1).isdigit() else v
        except ValueError:
            out[k] = v
    return out


def _policy(args):
    from ignosis_eval.stats.proportion import IntervalPolicy

    return IntervalPolicy(confidence=args.confidence, min_n=args.min_n)


def _print_summary(metrics: dict, out_dir) -> None:
    print(f"scoring written to {out_dir}")
    for w in metrics.get("warnings", []):
        print(f"WARNING: {w}")
    for name in ("critical_misses", "unsupported_pass_verdict", "integrity_failures", "critical_false_positive_gate",
                 "major_recall", "verdict_accuracy", "abstention_recall", "modality_conformance",
                 "verdict_consistency"):
        m = metrics["metrics"][name]
        if m.get("n") is None:
            print(f"  {name:32s} not measurable ({m.get('note')})")
        else:
            iv = m.get("interval")
            ivs = f" [{iv['low_pct']:.1f}%, {iv['high_pct']:.1f}%] {iv['method']}" if iv else " (no interval)"
            pct = "n/a" if m["pct"] is None else f"{m['pct']:.1f}%"
            print(f"  {name:32s} {m['k']}/{m['n']} = {pct}{ivs}")


def cmd_run(args) -> int:
    from ignosis_eval.contracts.enums import Split
    from ignosis_eval.runner.experiment import RunConfig, run_experiment

    cfg = RunConfig(
        benchmark_root=Path(args.benchmark_root), split=Split(args.split), evaluator=args.evaluator,
        repetitions=args.reps, seed=args.seed, runs_root=Path(args.runs_root), scoring_root=Path(args.scoring_root),
        profile_path=Path(args.profile), llm_backend=args.llm_backend, model_id=args.model_id,
        temperature=args.temperature, evaluator_options=_kv(args.evaluator_option), shuffle=args.shuffle,
        official=args.official, confirm_holdout=args.confirm_holdout, purpose=args.purpose,
        item_ids=args.items.split(",") if args.items else None, score=not args.no_score,
        interval_policy=_policy(args), reference_language=args.reference_language,
        invocation=["ignosis-eval", *sys.argv[1:]])
    res = run_experiment(cfg)
    c = res.completion
    print(f"run {res.run_id}: {c.status}; {c.n_records_written}/{c.n_item_reps_expected} records, "
          f"{c.n_item_reps_with_errors} item-reps with errors -> {res.run_dir}")
    if res.metrics is not None:
        _print_summary(res.metrics, res.scoring_dir)
    return 0


def cmd_score(args) -> int:
    from ignosis_eval.scoring.scorer import ScoringConfig, score_run

    out_dir, _ = score_run(args.run_dir, args.benchmark_root, scoring_root=args.scoring_root,
                           config=ScoringConfig(_policy(args), args.reference_language),
                           profile_path=args.profile, allow_incomplete=args.allow_incomplete,
                           out_suffix=args.suffix)
    from ignosis_eval.contracts.io import read_json

    _print_summary(read_json(out_dir / "metrics.json"), out_dir)
    return 0


def _add_stats_args(p) -> None:
    p.add_argument("--confidence", type=float, default=0.95)
    p.add_argument("--min-n", type=int, default=10, help="minimum n before any interval is reported")
    p.add_argument("--reference-language", default=None)


def cmd_schemas_export(args) -> int:
    from ignosis_eval.schemas import export_schemas

    paths = export_schemas(Path(args.out))
    print(f"wrote {len(paths)} schemas to {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ignosis-eval", description=__doc__)
    sub = p.add_subparsers(dest="group", required=True)

    cc = sub.add_parser("casecard", help="case-card authoring support").add_subparsers(dest="cmd", required=True)
    v = cc.add_parser("validate", help="validate case cards (required fields + authoring rules)")
    v.add_argument("paths", nargs="+")
    v.add_argument("--profile", default=None, help="also check ids against an evaluation profile")
    v.add_argument("--benchmark-root", default=None, help="also check cards against their input.json")
    v.add_argument("--set-checks", action="store_true", help="treat the given cards as a complete set (pairs)")
    v.set_defaults(func=cmd_casecard_validate)

    bm = sub.add_parser("benchmark", help="benchmark integrity").add_subparsers(dest="cmd", required=True)
    c = bm.add_parser("check", help="run all benchmark integrity checks")
    c.add_argument("--benchmark-root", default="benchmark")
    c.add_argument("--profile", default=DEFAULT_PROFILE)
    c.add_argument("--require-gold", action="store_true")
    c.set_defaults(func=cmd_benchmark_check)
    r = bm.add_parser("register-holdout", help="append current holdout cases to the holdout registry")
    r.add_argument("--benchmark-root", default="benchmark")
    r.set_defaults(func=cmd_benchmark_register_holdout)

    mf = sub.add_parser("manifest", help="benchmark manifest").add_subparsers(dest="cmd", required=True)
    b = mf.add_parser("build", help="hash the benchmark and write manifests/benchmark_manifest.json")
    b.add_argument("--benchmark-root", default="benchmark")
    b.add_argument("--dataset-name", required=True)
    b.add_argument("--dataset-version", required=True)
    b.add_argument("--created-by", required=True)
    b.add_argument("--profile", default=DEFAULT_PROFILE)
    b.add_argument("--notes", default=None)
    b.set_defaults(func=cmd_manifest_build)
    mv = mf.add_parser("verify", help="recompute hashes; exit non-zero on any drift")
    mv.add_argument("--benchmark-root", default="benchmark")
    mv.set_defaults(func=cmd_manifest_verify)

    gd = sub.add_parser("gold", help="gold freeze").add_subparsers(dest="cmd", required=True)
    f = gd.add_parser("freeze", help="canonicalize + hash gold, write gold manifest, mark read-only")
    f.add_argument("--benchmark-root", default="benchmark")
    f.add_argument("--gold-version", required=True)
    f.add_argument("--frozen-by", required=True)
    f.add_argument("--labeling-protocol-version", required=True)
    f.add_argument("--profile", default=DEFAULT_PROFILE)
    f.set_defaults(func=cmd_gold_freeze)
    gv = gd.add_parser("verify", help="verify gold hashes against the gold manifest")
    gv.add_argument("--benchmark-root", default="benchmark")
    gv.set_defaults(func=cmd_gold_verify)

    from ignosis_eval.evaluators.registry import EVALUATOR_NAMES

    rn = sub.add_parser("run", help="run an experiment (evaluate a split, store raw outputs, score)")
    rn.add_argument("--benchmark-root", default="benchmark")
    rn.add_argument("--split", required=True, choices=["dev", "holdout", "redteam", "calibration"])
    rn.add_argument("--evaluator", required=True, choices=list(EVALUATOR_NAMES))
    rn.add_argument("--reps", type=int, default=1, help="repetitions per item")
    rn.add_argument("--seed", type=int, required=True, help="randomization seed (recorded)")
    rn.add_argument("--llm-backend", default="mock", choices=["mock", "anthropic"])
    rn.add_argument("--model-id", default=None)
    rn.add_argument("--temperature", type=float, default=0.0)
    rn.add_argument("--evaluator-option", action="append", default=[], metavar="KEY=VALUE")
    rn.add_argument("--profile", default=DEFAULT_PROFILE)
    rn.add_argument("--runs-root", default="runs")
    rn.add_argument("--scoring-root", default="scoring")
    rn.add_argument("--shuffle", action="store_true", help="shuffle item order with the seed")
    rn.add_argument("--items", default=None, help="comma-separated subset of item ids (debug only)")
    rn.add_argument("--official", action="store_true", help="official run: clean git, frozen gold, full split")
    rn.add_argument("--confirm-holdout", action="store_true", help="required to run on the holdout split")
    rn.add_argument("--purpose", default=None)
    rn.add_argument("--no-score", action="store_true")
    _add_stats_args(rn)
    rn.set_defaults(func=cmd_run)

    sc = sub.add_parser("score", help="(re)score an existing run; outputs are write-once")
    sc.add_argument("--run-dir", required=True)
    sc.add_argument("--benchmark-root", default="benchmark")
    sc.add_argument("--scoring-root", default="scoring")
    sc.add_argument("--profile", default=None, help="defaults to the path recorded in the run manifest")
    sc.add_argument("--suffix", default=None, help="write to scoring/<run_id>--<suffix> (re-scoring)")
    sc.add_argument("--allow-incomplete", action="store_true")
    _add_stats_args(sc)
    sc.set_defaults(func=cmd_score)

    sx = sub.add_parser("schemas", help="export JSON Schemas of all contracts").add_subparsers(dest="cmd",
                                                                                               required=True)
    ex = sx.add_parser("export")
    ex.add_argument("--out", default="schemas")
    ex.set_defaults(func=cmd_schemas_export)
    return p


def main(argv: list[str] | None = None) -> int:
    from ignosis_eval.benchmark.checks import BenchmarkIntegrityError
    from ignosis_eval.integrity.freeze import IntegrityError
    from ignosis_eval.runner.experiment import RunConfigError
    from ignosis_eval.scoring.loader import ScoringError

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (IntegrityError, BenchmarkIntegrityError, ScoringError, RunConfigError) as exc:
        print(f"FAIL CLOSED: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())

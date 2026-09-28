"""Command-line interface: `ignosis-eval <group> <command> ...` (or `python -m ignosis_eval`).

    spec check | spec pending                      spec pack versions, PENDING_HUMAN_SIGNOFF inventory
    casecard validate <cards...>                   CCxxx rules
    bench check [--scope dev|private|all] [--require-gold]
    bench manifest --scope ... --dataset-version V  hash list (P-1 rule 5)
    gold freeze --scope ... --gold-version V        |  bench verify / gold verify --scope ...
    run --split dev --systems K0,A,A+,B [...]       P-5/P-6 run (locked kinds need --confirm-holdout)
    blind --run-id ID                               P-10 aliased view
    score --run-id ID [--suffix S] [--human-checks F]
    reveal --run-id ID --scoring-id S --report-sha256 H
    schemas export
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _print_issues(issues) -> int:
    errors = [i for i in issues if i.severity == "error"]
    for i in issues:
        print(str(i))
    print(f"{len(errors)} error(s), {len(issues) - len(errors)} warning(s)")
    return 1 if errors else 0


def _spec(args):
    from ignosis_eval.spec.loader import load_spec

    return load_spec(args.spec_dir, profile_path=args.profile)


def _layout(args):
    from ignosis_eval.benchmark.layout import BenchLayout

    return BenchLayout(args.bench_root, args.private_dir)


def _scopes(scope: str) -> tuple[str, ...]:
    return ("dev", "private") if scope == "all" else (scope,)


# ------------------------------------------------------------------------------------------ spec
def cmd_spec_check(args) -> int:
    s = _spec(args)
    print(f"contract {s.contract_version} · rubric {s.rubric_version} · profile {s.profile_id} "
          f"{s.profile_version} · canonical profile: {s.is_canonical_profile}")
    for name, sha in sorted(s.file_sha256.items()):
        print(f"  {name}  {sha}")
    return 0


def cmd_spec_pending(args) -> int:
    from ignosis_eval.spec.pending import RUN_BLOCKERS, find_pending

    s = _spec(args)
    for p in find_pending(s.rubric, s.profile):
        print(f"{'BLOCKS-LOCKED' if p.blocks_locked_run else 'non-blocking '}  {p.blocker or '?':<9} {p.path}  "
              f"— {p.note}")
    for b, what in RUN_BLOCKERS.items():
        print(f"run blocker    {b:<9} {what}")
    return 0


# ------------------------------------------------------------------------------------------ cards / bench
def cmd_casecard_validate(args) -> int:
    from ignosis_eval.benchmark.case_card_rules import check_card_rules, load_case_card_file

    spec = _spec(args)
    issues = []
    for p in args.paths:
        card, errs = load_case_card_file(p)
        issues += errs
        if card is not None:
            issues += check_card_rules(card, spec.registry)
    return _print_issues(issues)


def cmd_bench_check(args) -> int:
    from ignosis_eval.benchmark.checks import check_bench

    rep = check_bench(_layout(args), _spec(args), scopes=_scopes(args.scope), require_gold=args.require_gold)
    return _print_issues(rep.issues)


def _validator(args, scope: str, require_gold: bool):
    from ignosis_eval.benchmark.checks import check_bench

    def run() -> list[str]:
        rep = check_bench(_layout(args), _spec(args), scopes=(scope,), require_gold=require_gold)
        return [str(i) for i in rep.errors]

    return run


def cmd_bench_manifest(args) -> int:
    from ignosis_eval.integrity.freeze import build_bench_manifest
    from ignosis_eval.pipeline.normalize import unit_facts

    m = build_bench_manifest(_layout(args), args.scope, dataset_name=args.dataset_name,
                             dataset_version=args.dataset_version, created_by=args.created_by, facts_fn=unit_facts,
                             validator=_validator(args, args.scope, False), notes=args.notes)
    print(f"{args.scope} manifest {m.dataset_name}@{m.dataset_version}: {len(m.items)} items, "
          f"dataset_hash={m.dataset_hash}")
    return 0


def cmd_bench_verify(args) -> int:
    from ignosis_eval.integrity.freeze import verify_bench

    m, sha = verify_bench(_layout(args), args.scope)
    print(f"OK {args.scope} {m.dataset_name}@{m.dataset_version} manifest_sha256={sha}")
    return 0


def cmd_gold_freeze(args) -> int:
    from ignosis_eval.integrity.freeze import freeze_gold

    m = freeze_gold(_layout(args), args.scope, gold_version=args.gold_version, frozen_by=args.frozen_by,
                    labeling_protocol_version=args.labeling_protocol_version,
                    validator=_validator(args, args.scope, True))
    print(f"gold {m.gold_version} frozen ({args.scope}): {len(m.entries)} labels, gold_hash={m.gold_hash}")
    return 0


def cmd_gold_verify(args) -> int:
    from ignosis_eval.integrity.freeze import verify_gold

    m, sha = verify_gold(_layout(args), args.scope)
    print(f"OK gold {m.gold_version} ({args.scope}) manifest_sha256={sha}")
    return 0


# ------------------------------------------------------------------------------------------ run / blind / score
def cmd_run(args) -> int:
    from ignosis_eval.contracts.enums import Split, System
    from ignosis_eval.pipeline.asr import ReplayASR
    from ignosis_eval.runner.experiment import RunConfig, run_experiment

    systems = [System(s.strip()) for s in args.systems.split(",") if s.strip()]
    asr = None
    if args.asr_cache:
        asr = ReplayASR(engine=args.asr_engine, version=args.asr_version, cache_dir=Path(args.asr_cache))
    cfg = RunConfig(bench_root=Path(args.bench_root), private_root=Path(args.private_dir) if args.private_dir else None,
                    split=Split(args.split), systems=systems, kind=args.kind, base_seed=args.base_seed,
                    repetitions=args.repetitions, results_root=Path(args.results_root),
                    spec_dir=Path(args.spec_dir) if args.spec_dir else None,
                    profile_path=Path(args.profile) if args.profile else None, llm_backend=args.llm_backend,
                    replay_dir=Path(args.replay_dir) if args.replay_dir else None, model_id=args.model_id,
                    max_tokens=args.max_tokens, tuning_round=args.tuning_round, confirm_holdout=args.confirm_holdout,
                    item_ids=args.items.split(",") if args.items else None, purpose=args.purpose,
                    invocation=["ignosis-eval", *sys.argv[1:]])
    res = run_experiment(cfg, asr=asr)
    print(f"run {res.run_id}: {res.completion.status} — {res.completion.n_records_written} records "
          f"({res.completion.n_evaluation_failed} EVALUATION_FAILED) in {res.run_dir}")
    return 0


def cmd_blind(args) -> int:
    from ignosis_eval.runner.blind import build_view
    from ignosis_eval.runner.storage import ResultsLayout

    print(build_view(ResultsLayout(Path(args.results_root)), args.run_id))
    return 0


def cmd_score(args) -> int:
    from ignosis_eval.runner.storage import ResultsLayout
    from ignosis_eval.scoring.scorer import score_view

    results = ResultsLayout(Path(args.results_root))
    out = score_view(results.blinded / args.run_id, _layout(args), _spec(args), results.scoring, suffix=args.suffix,
                     confirmations_path=args.human_checks, sample_seed=args.sample_seed)
    print(out)
    return 0


def cmd_reveal(args) -> int:
    from ignosis_eval.runner.blind import reveal
    from ignosis_eval.runner.storage import ResultsLayout

    print(reveal(ResultsLayout(Path(args.results_root)), args.run_id, args.scoring_id, args.report_sha256))
    return 0


def cmd_schemas_export(args) -> int:
    from ignosis_eval.schemas import export_schemas

    for p in export_schemas(Path(args.out)):
        print(p)
    return 0


# ------------------------------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ignosis-eval")
    sub = ap.add_subparsers(dest="group", required=True)

    def common(p, bench: bool = True):
        p.add_argument("--spec-dir", default=None, help="spec pack directory (default docs/spec)")
        p.add_argument("--profile", default=None, help="profile override (non-canonical: dev/tests only)")
        if bench:
            p.add_argument("--bench-root", default="bench")
            p.add_argument("--private-dir", default=None, help="default: $BENCH_PRIVATE_DIR")

    g = sub.add_parser("spec").add_subparsers(dest="cmd", required=True)
    p = g.add_parser("check")
    common(p, bench=False)
    p.set_defaults(func=cmd_spec_check)
    p = g.add_parser("pending")
    common(p, bench=False)
    p.set_defaults(func=cmd_spec_pending)

    g = sub.add_parser("casecard").add_subparsers(dest="cmd", required=True)
    p = g.add_parser("validate")
    common(p, bench=False)
    p.add_argument("paths", nargs="+")
    p.set_defaults(func=cmd_casecard_validate)

    g = sub.add_parser("bench").add_subparsers(dest="cmd", required=True)
    p = g.add_parser("check")
    common(p)
    p.add_argument("--scope", choices=["dev", "private", "all"], default="dev")
    p.add_argument("--require-gold", action="store_true")
    p.set_defaults(func=cmd_bench_check)
    p = g.add_parser("manifest")
    common(p)
    p.add_argument("--scope", choices=["dev", "private"], required=True)
    p.add_argument("--dataset-name", default="bench-a1")
    p.add_argument("--dataset-version", required=True)
    p.add_argument("--created-by", required=True)
    p.add_argument("--notes", default=None)
    p.set_defaults(func=cmd_bench_manifest)
    p = g.add_parser("verify")
    common(p)
    p.add_argument("--scope", choices=["dev", "private"], required=True)
    p.set_defaults(func=cmd_bench_verify)

    g = sub.add_parser("gold").add_subparsers(dest="cmd", required=True)
    p = g.add_parser("freeze")
    common(p)
    p.add_argument("--scope", choices=["dev", "private"], required=True)
    p.add_argument("--gold-version", required=True)
    p.add_argument("--frozen-by", required=True)
    p.add_argument("--labeling-protocol-version", required=True)
    p.set_defaults(func=cmd_gold_freeze)
    p = g.add_parser("verify")
    common(p)
    p.add_argument("--scope", choices=["dev", "private"], required=True)
    p.set_defaults(func=cmd_gold_verify)

    p = sub.add_parser("run")
    common(p)
    p.add_argument("--split", choices=["dev", "holdout", "redteam"], required=True)
    p.add_argument("--systems", required=True, help="comma-separated: K0,A,A+,B")
    p.add_argument("--kind", choices=["dev", "dev_tuning", "locked_holdout", "locked_redteam"], default="dev")
    p.add_argument("--tuning-round", type=int, default=None)
    p.add_argument("--repetitions", type=int, default=5)
    p.add_argument("--base-seed", type=int, required=True)
    p.add_argument("--results-root", default=".")
    p.add_argument("--llm-backend", choices=["mock_replay", "anthropic"], default="mock_replay")
    p.add_argument("--replay-dir", default=None)
    p.add_argument("--model-id", default=None)
    p.add_argument("--max-tokens", type=int, default=None)
    p.add_argument("--asr-cache", default=None, help="cached ASR results (ASR engine PENDING B-06)")
    p.add_argument("--asr-engine", default="replay")
    p.add_argument("--asr-version", default="0")
    p.add_argument("--items", default=None, help="dev only: comma-separated item subset")
    p.add_argument("--confirm-holdout", action="store_true")
    p.add_argument("--purpose", default=None)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("blind")
    p.add_argument("--run-id", required=True)
    p.add_argument("--results-root", default=".")
    p.set_defaults(func=cmd_blind)

    p = sub.add_parser("score")
    common(p)
    p.add_argument("--run-id", required=True)
    p.add_argument("--results-root", default=".")
    p.add_argument("--suffix", default=None)
    p.add_argument("--human-checks", default=None, help="JSON of human check results keyed by check_id")
    p.add_argument("--sample-seed", type=int, default=None)
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("reveal")
    p.add_argument("--run-id", required=True)
    p.add_argument("--scoring-id", required=True)
    p.add_argument("--report-sha256", required=True)
    p.add_argument("--results-root", default=".")
    p.set_defaults(func=cmd_reveal)

    g = sub.add_parser("schemas").add_subparsers(dest="cmd", required=True)
    p = g.add_parser("export")
    p.add_argument("--out", default="schemas")
    p.set_defaults(func=cmd_schemas_export)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except Exception as exc:  # CLI surface: print and fail (non-zero), never swallow
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

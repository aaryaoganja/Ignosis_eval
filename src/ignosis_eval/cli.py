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
    return p


def main(argv: list[str] | None = None) -> int:
    from ignosis_eval.benchmark.checks import BenchmarkIntegrityError
    from ignosis_eval.integrity.freeze import IntegrityError

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (IntegrityError, BenchmarkIntegrityError) as exc:
        print(f"FAIL CLOSED: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())

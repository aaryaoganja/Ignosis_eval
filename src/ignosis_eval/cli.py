"""Command-line interface: `ignosis-eval <group> <command> ...` (or `python -m ignosis_eval`).

    spec check | spec pending                      spec pack versions, PENDING_HUMAN_SIGNOFF inventory
    casecard validate <cards...>                   CCxxx rules
    bench check [--scope dev|private|all] [--require-gold]   (dev includes the frozen DEV design in bench/public)
    bench public-check                              validate bench/public only (PDxxx rules; no private data)
    bench transcript-qc DIR [--items ID ...]        QC draft DEV transcripts <ITEM_ID>.txt|.json (TQxxx rules)
    bench manifest --scope ... --dataset-version V  hash list (P-1 rule 5)
    gold freeze --scope ... --gold-version V        |  bench verify / gold verify --scope ...
    run --split dev --systems K0,A,A+,B [...]       P-5/P-6 run (locked kinds need --confirm-holdout)
    blind --run-id ID                               P-10 aliased view
    score --run-id ID [--suffix S] [--human-checks F]
    reveal --run-id ID --scoring-id S --report-sha256 H
    schemas export
    dev run [--systems K0,A,A+,B] [--llm-backend gemini] [--reps k]   DEV draft run (not a protocol run; needs
                                                   GEMINI_API_KEY at runtime for A / B)
    dev report --run-id ID [--out DIR] [--price-in USD --price-out USD]           intent-referenced baseline report
    dev consistency [--items ...] [--reps 3] [--systems K0]                        reproducibility smoke run
    dev smoke                                        live Gemini check (A, A+, B on one synthetic demo call)
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
    if rep.public_dev is not None and rep.public_dev.design is not None:
        d = rep.public_dev.design
        print(f"frozen DEV design: {len(d.items)} items, FREEZE-public {d.freeze_public_sha256[:16]}; "
              f"{len(rep.items)} dev item(s) with transcripts")
    return _print_issues(rep.issues)


def cmd_bench_public_check(args) -> int:
    from ignosis_eval.benchmark.public_dev import validate_public_dev

    rep = validate_public_dev(_layout(args).public_dir, _spec(args))
    if rep.design is not None:
        packs: dict[str, int] = {}
        for it in rep.design.items.values():
            packs[it.pack.value] = packs.get(it.pack.value, 0) + 1
        reg = rep.design.registries()
        print(f"frozen DEV design: {len(rep.design.items)} items {dict(sorted(packs.items()))}; pairs "
              f"{[p.pair_id for p in reg.pairs]}; controls {[c.item_id for c in reg.controls]}; "
              f"verified against docs/freeze (FREEZE-public {rep.design.freeze_public_sha256[:16]})")
    return _print_issues(rep.issues)


def cmd_bench_transcript_qc(args) -> int:
    from ignosis_eval.benchmark.transcript_qc import check_transcripts

    rep = check_transcripts(args.directory, _spec(args), _layout(args).public_dir, items=args.items)
    for iid in sorted(rep.checked):
        found = rep.item_issues(iid)
        n_err = sum(1 for i in found if i.severity == "error")
        state = f"{n_err} error(s), {len(found) - n_err} warning(s)" if found else "no deterministic issue"
        print(f"{iid:<8} {rep.checked[iid].name:<14} {state}")
    print(f"pairs checked: {rep.pairs_checked or 'none'}; semantic QC (beats, gate behavior, verdict, "
          "naturalness) is the reviewer's")
    return _print_issues(rep.issues)


def _draft_cfg(args, **kw):
    from ignosis_eval.contracts.enums import System
    from ignosis_eval.runner.dev_drafts import DraftRunConfig

    systems = [System(s.strip()) for s in args.systems.split(",") if s.strip()]
    return DraftRunConfig(drafts_dir=Path(args.drafts), bench_root=Path(args.bench_root), systems=systems,
                          repetitions=args.reps, base_seed=args.seed, results_root=Path(args.results_root),
                          spec_dir=Path(args.spec_dir) if args.spec_dir else None, llm_backend=args.llm_backend,
                          model_id=args.model, replay_dir=Path(args.replay_dir) if args.replay_dir else None,
                          max_tokens=args.max_tokens, item_ids=args.items, invocation=["ignosis-eval", *sys.argv[1:]],
                          **kw)


def cmd_dev_run(args) -> int:
    from ignosis_eval.runner.dev_drafts import DraftRunError, run_dev_drafts

    try:
        res = run_dev_drafts(_draft_cfg(args))
    except DraftRunError as exc:
        print(str(exc))
        return 2
    print(f"{res.run_id}: {res.completion['status']} · {res.completion['n_records_written']} records · "
          f"{res.run_dir}")
    return 0


def cmd_dev_report(args) -> int:
    import json

    from ignosis_eval.benchmark.public_dev import validate_public_dev
    from ignosis_eval.devbaseline.report import build_report, render_markdown
    from ignosis_eval.runner.dev_drafts import DRAFT_RUNS_DIR, evaluator_configuration

    spec = _spec(args)
    run_dir = Path(args.results_root) / DRAFT_RUNS_DIR / args.run_id
    manifest = json.loads((run_dir / "draft_run.json").read_text(encoding="utf-8"))
    from ignosis_eval.evaluators import provider_config

    llm = manifest.get("llm") or {}
    pending = None if provider_config.api_key_configured() else (
        f"PROVIDER KEY MISSING: {provider_config.API_KEY_ENV} is not set in the runtime environment, so the "
        f"{provider_config.PROVIDER} evaluator ({provider_config.settings().model_id}) could not run")
    not_executed = {s: pending or "not requested in this run" for s in ("A", "A+", "B")}
    design = validate_public_dev(_layout(args).public_dir, spec).design
    if design is None:
        print("the frozen DEV design does not validate")
        return 2
    prices = (args.price_in, args.price_out) if args.price_in is not None and args.price_out is not None else None
    consistency = None
    if args.consistency_run_id:
        cpath = Path(args.results_root) / DRAFT_RUNS_DIR / args.consistency_run_id / "consistency.json"
        consistency = json.loads(cpath.read_text(encoding="utf-8"))
    report = build_report(run_dir, design, _layout(args).public_dir, consistency=consistency,
                          quote_match_min=float(spec.threshold("quote_match_min")),
                          evaluator_configuration=evaluator_configuration(spec, llm.get("backend"),
                                                                          llm.get("model_snapshot_id")),
                          not_executed=not_executed, prices=prices)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "dev-baseline.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "dev-baseline.md").write_text(render_markdown(report), encoding="utf-8")
    for row in report["summary_table"]:
        print(" | ".join(f"{k}: {v}" for k, v in row.items()))
    print(f"wrote {out / 'dev-baseline.json'} and {out / 'dev-baseline.md'}")
    return 0


def cmd_dev_smoke(args) -> int:
    import json

    from ignosis_eval.app.smoke import run_smoke
    from ignosis_eval.evaluators.llm import ProviderConfigError

    try:
        out = run_smoke(_spec(args))
    except ProviderConfigError as exc:
        print(f"NOT RUN: {exc}")
        return 2
    for c in out["checks"]:
        print(f"{c['status']:4}  {c['check']}  ({c['detail']})")
    print(json.dumps({k: out[k] for k in ("provider", "model", "served_model_versions", "record_system", "records",
                                          "note")}, indent=1))
    return 0 if out["ok"] else 1


def cmd_dev_consistency(args) -> int:
    import json

    from ignosis_eval.devbaseline.metrics import load_run
    from ignosis_eval.runner.dev_drafts import DraftRunError, frontend_stability, run_dev_drafts
    from ignosis_eval.runner.storage import write_json_once

    cfg = _draft_cfg(args, consistency=True)
    try:
        res = run_dev_drafts(cfg)
    except DraftRunError as exc:
        print(str(exc))
        return 2
    manifest, by_system = load_run(res.run_dir)
    records = {s: {i: sorted({r.content_hash()[:16] for r in u.records}) for i, u in units.items()}
               for s, units in by_system.items()}
    summary = {"run_id": res.run_id, "repetitions": cfg.repetitions,
               "frontend_distinct_input_hashes": frontend_stability(cfg, cfg.repetitions),
               "record_distinct_content_hashes": {s: {i: len(h) for i, h in d.items()} for s, d in records.items()},
               "system_configs": manifest["systems"], "component_versions": manifest["component_versions"],
               "spec": manifest["spec"]}
    summary["stable"] = all(v == 1 for v in summary["frontend_distinct_input_hashes"].values()) and all(
        n == 1 for d in summary["record_distinct_content_hashes"].values() for n in d.values())
    write_json_once(res.run_dir / "consistency.json", summary)
    print(json.dumps({k: summary[k] for k in ("run_id", "repetitions", "stable", "frontend_distinct_input_hashes",
                                              "record_distinct_content_hashes")}, indent=1))
    return 0 if summary["stable"] else 1


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
    p = g.add_parser("public-check")
    common(p)
    p.set_defaults(func=cmd_bench_public_check)
    p = g.add_parser("transcript-qc")
    common(p)
    p.add_argument("directory", help="draft transcripts named <ITEM_ID>.txt or .json")
    p.add_argument("--items", nargs="+", default=None, help="check only these item ids")
    p.set_defaults(func=cmd_bench_transcript_qc)
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
    p.add_argument("--llm-backend", choices=["mock_replay", "gemini", "openai", "anthropic"], default="mock_replay")
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

    g = sub.add_parser("dev").add_subparsers(dest="cmd", required=True)
    for name, fn, reps, systems in (("run", cmd_dev_run, 1, "K0"), ("consistency", cmd_dev_consistency, 3, "K0")):
        p = g.add_parser(name)
        common(p)
        p.add_argument("--drafts", default="bench/dev/transcripts")
        p.add_argument("--systems", default=systems, help="comma list of K0,A,A+,B")
        p.add_argument("--reps", type=int, default=reps)
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--results-root", default=".")
        p.add_argument("--llm-backend", choices=["gemini", "mock_replay", "openai", "anthropic"], default="gemini",
                       help="gemini: key from the runtime env GEMINI_API_KEY only")
        p.add_argument("--model", default=None, help="pinned model id (default: evaluators/provider_config.py)")
        p.add_argument("--replay-dir", default=None)
        p.add_argument("--max-tokens", type=int, default=None)
        p.add_argument("--items", nargs="+", default=None)
        p.set_defaults(func=fn)
    p = g.add_parser("smoke", help="live provider smoke test: one synthetic demo call through A, A+ and B")
    common(p)
    p.set_defaults(func=cmd_dev_smoke)
    p = g.add_parser("report")
    common(p)
    p.add_argument("--run-id", required=True)
    p.add_argument("--results-root", default=".")
    p.add_argument("--out", default="reports/dev-baseline")
    p.add_argument("--price-in", type=float, default=None, help="USD per million input tokens (estimate)")
    p.add_argument("--price-out", type=float, default=None, help="USD per million output tokens (estimate)")
    p.add_argument("--consistency-run-id", default=None, help="include a `dev consistency` run's summary")
    p.set_defaults(func=cmd_dev_report)

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

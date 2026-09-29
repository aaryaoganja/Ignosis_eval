"""DEV draft baseline report: a machine-readable JSON and a human-readable Markdown rendering of it."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from ignosis_eval.benchmark.public_dev import DevDesign
from ignosis_eval.devbaseline.metrics import (
    SYSTEM_ORDER,
    UNMEASURED,
    build_reference,
    compare_systems,
    load_run,
    per_item,
    system_metrics,
)

REPORT_VERSION = "dev-baseline/1.1.0"
LABEL = "DEV ENGINEERING MEASUREMENT — NOT FINAL RELIABILITY EVIDENCE"
FINAL_VALIDATION = "FINAL RELIABILITY VALIDATION: PENDING"
REFERENCE = "frozen DEV design intent (bench/public blueprint and case cards), NOT gold"


def build_report(run_dir: Path, design: DevDesign, public_dir: Path, *, quote_match_min: float,
                 evaluator_configuration: dict[str, Any], not_executed: dict[str, str] | None = None,
                 prices: tuple[float, float] | None = None, consistency: dict[str, Any] | None = None
                 ) -> dict[str, Any]:
    manifest, by_system = load_run(run_dir)
    items = list(manifest["items"]["executed"])
    refs = build_reference(design, public_dir, items)
    systems: dict[str, Any] = {}
    for name in SYSTEM_ORDER:
        if name in by_system:
            systems[name] = system_metrics(refs, by_system[name], threshold=quote_match_min, prices=prices)
        else:
            systems[name] = {"status": "NOT_EXECUTED",
                             "reason": (not_executed or {}).get(name, "not requested in this run")}
    fe = manifest["frontend"]
    fe_eval = sum(fe[i]["evaluability"] == refs[i].evaluability for i in items)
    fe_g7 = sum(fe[i]["prechecks"].get("G7") == refs[i].gates.get("G7") for i in items)
    rows = per_item(refs, by_system)
    counts = {s: dict(Counter(d["class"] for r in rows.values() for d in r["systems"].get(s, {})
                              .get("disagreements", []))) for s in by_system}
    return {
        "report": "dev-baseline", "report_version": REPORT_VERSION, "label": LABEL, "notice": manifest["notice"],
        "reference": REFERENCE, "not_reliability_evidence": True, "final_reliability_validation": "PENDING",
        "transcripts": {"status": "DRAFT", "human_review_pending": manifest["drafts"]["human_review_pending"],
                        "assisting_families": manifest["drafts"]["assisting_families"],
                        "provenance_sha256": manifest["drafts"]["provenance_sha256"]},
        "run": {"run_id": manifest["run_id"], "created_at": manifest["created_at"],
                "repetitions": manifest["repetitions"], "consistency_mode": manifest["consistency_mode"],
                "git_commit": manifest["git"].get("commit"), "git_dirty": manifest["git"].get("dirty"),
                "items_executed": items, "items_excluded": manifest["items"]["excluded"],
                "unit_mode": manifest["unit_mode"], "asr_mode": manifest["asr_mode"],
                "p17_payload_strings_checked": manifest["p17_payload_strings_checked"]},
        "spec": manifest["spec"], "component_versions": manifest["component_versions"],
        "evaluator_configuration": evaluator_configuration,
        "frontend": {"evaluability_agreement": {"num": fe_eval, "den": len(items)},
                     "g7_agreement": {"num": fe_g7, "den": len(items)},
                     "per_item": {i: {"evaluability": fe[i]["evaluability"], "G7": fe[i]["prechecks"].get("G7"),
                                      "intended_G7": refs[i].gates.get("G7")} for i in items},
                     "pending_steps": sorted({s for i in items for s in fe[i]["steps_pending"]})},
        "systems": systems, "summary_table": summary_rows(systems), "comparison": compare_systems(by_system, refs),
        "disagreement_counts": counts, "consistency_smoke": consistency, "per_item": rows,
    }


def _pct(r: Any) -> str:
    if not isinstance(r, dict) or r.get("value") in (None, UNMEASURED):
        return UNMEASURED if not isinstance(r, dict) or not r.get("den") else f"{r['num']}/{r['den']}"
    return f"{100 * r['value']:.0f}% ({r['num']}/{r['den']})"


def summary_rows(systems: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    for name, m in systems.items():
        if m.get("status") != "EXECUTED":
            rows.append({"evaluator": name, "status": f"NOT EXECUTED: {m.get('reason', '')}"})
            continue
        ab = m["abstention"]
        lat = m["latency_s"]
        cost = m["cost"]["usd"]
        rows.append({
            "evaluator": name, "status": "EXECUTED",
            "verdict_accuracy": _pct(m["verdict_accuracy"]), "critical_recall": _pct(m["critical_recall"]),
            "must_not_fire_precision": _pct(m["must_not_fire_precision"]), "pair_accuracy": _pct(m["pair_accuracy"]),
            "abstention": f"EF {ab['evaluation_failed']}/{ab['records']} · NE {ab['not_evaluable']} · "
                          f"PARTIAL {ab['partial']}",
            "latency": f"p50 {lat['p50'] * 1000:.1f} ms · p95 {lat['p95'] * 1000:.1f} ms" if lat["p50"] is not None
            else UNMEASURED,
            "cost": f"${cost}" if isinstance(cost, (int, float)) else str(cost)})
    return rows


def _tags(dw: str | None, cl: bool | None) -> str:
    return ", ".join(([f"DW {dw}"] if dw and dw != "NONE" else []) + (["Clean Loss"] if cl else []))


def render_markdown(r: dict[str, Any]) -> str:
    out: list[str] = []
    add = out.append
    add(f"# {LABEL}\n")
    add(f"DEV draft baseline · **{FINAL_VALIDATION}** (no gold, no holdout, drafts pending human review)\n")
    add(f"> **{r['notice']}**\n>\n> Reference: {r['reference']}. Transcripts: **{r['transcripts']['status']}**, "
        f"human review pending: **{r['transcripts']['human_review_pending']}**. This is the first DEV baseline, not "
        "a validation of the evaluator.\n")
    run = r["run"]
    add(f"Run `{run['run_id']}` · commit `{(run['git_commit'] or '')[:12]}` · repetitions {run['repetitions']} · "
        f"unit mode {run['unit_mode']} · ASR: {run['asr_mode']} · P-17 strings checked: "
        f"{run['p17_payload_strings_checked']}\n")
    add("## Evaluator configuration\n")
    add("| Field | Value |\n|---|---|")
    for k, v in r["evaluator_configuration"].items():
        add(f"| {k} | {v if not isinstance(v, dict) else ', '.join(f'{a}: {b}' for a, b in v.items())} |")
    add("\n## Summary\n")
    add("| Evaluator | Verdict accuracy | Critical recall | Must-not-fire precision | Pair accuracy | Abstention | "
        "Latency | Cost |\n|---|---:|---:|---:|---:|---:|---:|---:|")
    for row in r["summary_table"]:
        if row["status"] != "EXECUTED":
            add(f"| {row['evaluator']} | {row['status']} | | | | | | |")
        else:
            add(f"| {row['evaluator']} | {row['verdict_accuracy']} | {row['critical_recall']} | "
                f"{row['must_not_fire_precision']} | {row['pair_accuracy']} | {row['abstention']} | {row['latency']} | "
                f"{row['cost']} |")
    add("\nPercentages are agreement with the design intent over the executed DEV drafts (small n; not reliability "
        "evidence). UNMEASURED means the metric has no support in this run.\n")
    add("## Further metrics\n")
    for name, m in r["systems"].items():
        if m.get("status") != "EXECUTED":
            continue
        add(f"**{name}** · defect precision {_pct(m['defect_precision'])} · defect recall {_pct(m['defect_recall'])} "
            f"· evidence faithfulness {_pct(m['evidence_faithfulness'])} · attribution agreement "
            f"{_pct(m['attribution_agreement'])} · dangerous-win agreement {_pct(m['dangerous_win_agreement'])} · "
            f"clean-loss agreement {_pct(m['clean_loss_agreement'])} · "
            f"evaluability agreement {_pct(m['abstention']['evaluability_agreement'])} · consistency "
            f"{m['consistency'].get('value', m['consistency'])} · gate false fires {m['gate_false_fires'] or 'none'} · "
            f"tokens {m['tokens']} · cost note: {m['cost']['note']}\n")
        if m["abstention"]["inconclusive_checks"]:
            add(f"  INCONCLUSIVE checks: {m['abstention']['inconclusive_checks']}\n")
        if m["pairs"]:
            add("  Pairs: " + "; ".join(f"{p}: target {v['target']}, violating {'ok' if v['violating_correct'] else 'MISS'}"
                                        f", clean {'ok' if v['clean_correct'] else 'FALSE ALARM'}"
                                        for p, v in m["pairs"].items()) + "\n")
    add("## Deterministic front end (shared by every system)\n")
    fe = r["frontend"]
    add(f"Evaluability agreement {fe['evaluability_agreement']['num']}/{fe['evaluability_agreement']['den']} · "
        f"G7 agreement {fe['g7_agreement']['num']}/{fe['g7_agreement']['den']} (header items G-02 and K-07 PASS, "
        "others OUT_OF_SCOPE; bench-a1 has no G7 positive, so G7 recall is UNMEASURED).\n")
    add(f"Front-end steps still pending sign-off or not built: {', '.join(fe['pending_steps'])}\n")
    add("Excluded from the call-level baseline: " + "; ".join(f"{k} ({v})" for k, v in run["items_excluded"].items())
        + "\n")
    cs = r.get("consistency_smoke")
    add("## Reproducibility smoke run\n")
    if cs:
        n_fe = sum(v == 1 for v in cs["frontend_distinct_input_hashes"].values())
        recs = {s: f"{sum(n == 1 for n in d.values())}/{len(d)}" for s, d in
                cs["record_distinct_content_hashes"].items()}
        add(f"Run `{cs['run_id']}`, {cs['repetitions']} repetitions under the same settings: front-end input hash "
            f"stable on {n_fe}/{len(cs['frontend_distinct_input_hashes'])} items; record content hash stable per "
            f"system: {recs}; overall stable: **{cs['stable']}**. System configurations, prompt hashes and component "
            "versions are captured in the run's `draft_run.json`.\n")
    else:
        add("Not run.\n")
    add("## A vs A+ vs B\n")
    cmp_ = r["comparison"]
    if len(cmp_["systems"]) < 2:
        add(f"Not comparable in this run: only {cmp_['systems'] or 'no system'} executed.\n")
    else:
        for k, v in cmp_["pairwise_verdict_agreement"].items():
            add(f"- {k}: verdict agreement {_pct(v)}")
        if "aplus_changed_verdict" in cmp_:
            add(f"- A+ changed A's verdict on: {cmp_['aplus_changed_verdict'] or 'none'}")
        add("")
    add("## Disagreements by class\n")
    add("Classes: `evaluator_failure`, `benchmark_ambiguity` (design label not Sure, contested or with an ambiguity "
        "note), `unsupported_or_oos` (the system reported the check INCONCLUSIVE / OUT_OF_SCOPE or its evidence did "
        "not verify), `agent_behavior_error_detected` (an intended agent defect that was found). Labels are never "
        "changed to match the evaluator.\n")
    for s, c in r["disagreement_counts"].items():
        add(f"- {s}: {c or 'none'}")
    add("\n## Per item\n")
    names = [s for s in SYSTEM_ORDER if r["systems"].get(s, {}).get("status") == "EXECUTED"]
    add("| Item | Intended verdict | Intended defects | " + " | ".join(names) + " |\n|---|---|---|"
        + "---|" * len(names))
    for iid, row in r["per_item"].items():
        cells = []
        for s in names:
            sr = row["systems"].get(s)
            if sr is None:
                cells.append("-")
                continue
            got = sorted(set(sr["fired_gates"]) | set(sr["codes"]))
            tags = _tags(sr.get("dangerous_win"), sr.get("clean_loss"))
            cells.append(f"{sr['verdict']}{' ' + sr['critical_status'] if sr['critical_status'] else ''}"
                         f"{' · ' + ', '.join(got) if got else ''}{' · ' + tags if tags else ''}")
        intended = sorted(set(row["intended"]["fired_gates"]) | set(row["intended"]["codes"]))
        itags = _tags(row["intended"].get("dangerous_win"), row["intended"].get("clean_loss"))
        add(f"| {iid} | {row['intended']['verdict']}{' · ' + itags if itags else ''} | {', '.join(intended) or '-'} | "
            + " | ".join(cells) + " |")
    add("\n### Evidence, confidence and attribution (first repetition)\n")
    for iid, row in r["per_item"].items():
        for s, sr in row["systems"].items():
            ev = sr["evidence"]
            dis = [d for d in sr["disagreements"] if d["class"] != "agent_behavior_error_detected"]
            if not ev and not dis:
                continue
            add(f"- **{iid} / {s}**")
            for e in ev:
                add(f"  - {e['check']}: turn {e.get('turn')} \"{e.get('quote')}\" · confidence {e.get('confidence')} "
                    f"· attribution {e.get('attribution')}")
            for d in dis:
                add(f"  - {d['class']}: {d['check']} ({d['detail']})")
    return "\n".join(out) + "\n"


__all__ = ["FINAL_VALIDATION", "LABEL", "REPORT_VERSION", "build_report", "render_markdown", "summary_rows"]

/* Ignosis Call Quality Judge: single-page client. It talks only to this server's /api; it never holds a provider
   key. Every dynamic string is inserted with textContent (no HTML injection). */
"use strict";

const state = { config: null, demos: [], mode: "transcript", demoId: null, lastResult: null };
const view = document.getElementById("view");

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  let body = null;
  try { body = await res.json(); } catch (e) { body = null; }
  if (!res.ok) {
    const err = (body && body.error) || { code: "HTTP_" + res.status, message: "Request failed (" + res.status + ").", pathway: [] };
    throw err;
  }
  return body;
}

function badge(text, cls) { return h("span", { class: "badge " + (cls || "plain") }, text); }
function pct(m) {
  if (!m || m.value === null || m.value === undefined || m.value === "UNMEASURED") return m && m.den ? m.num + "/" + m.den : "UNMEASURED";
  return Math.round(m.value * 100) + "% (" + m.num + "/" + m.den + ")";
}
function setActive(route) {
  document.querySelectorAll(".nav a").forEach(a => a.classList.toggle("active", a.dataset.route === route));
}
function errorNotice(err) {
  return h("div", { class: "notice err", role: "alert" },
    h("b", {}, err.message || "Something went wrong."),
    (err.pathway && err.pathway.length) ? h("ul", {}, err.pathway.map(p => h("li", {}, p))) : null);
}

/* ------------------------------------------------------------------ Screen 1: Evaluate a Call */
function renderEvaluate() {
  setActive("evaluate");
  const cfg = state.config;
  const live = cfg && cfg.live_available;
  const modes = (cfg && cfg.modes) || [];
  const modeInfo = modes.find(m => m.id === state.mode) || {};
  const out = h("div", { id: "eval-out" });

  const transcriptBox = h("textarea", { id: "transcript", spellcheck: "false",
    placeholder: "# call_start_ts: 2026-09-28T14:05:00+05:30   (optional header)\nAGENT: Namaste, main ... bol rahi hoon.\nBORROWER: Haan ji, boliye." });
  const demo = state.demos.find(d => d.id === state.demoId);
  if (demo) transcriptBox.value = demo.transcript;
  transcriptBox.addEventListener("input", () => {
    if (demo && transcriptBox.value.trim() !== demo.transcript.trim()) {
      state.demoId = null; replayRow.hidden = true;
      document.querySelectorAll(".demo").forEach(b => b.classList.remove("selected"));
    }
  });
  const tFile = h("input", { type: "file", id: "transcript-file", accept: ".txt,.json" });
  const aFile = h("input", { type: "file", id: "audio-file", accept: ".wav,.mp3,.m4a" });
  const callName = h("input", { type: "text", id: "call-name", maxlength: "80", placeholder: "e.g. Branch 12 - evening batch, call 4" });
  const replayBox = h("input", { type: "checkbox", id: "replay", checked: !live || null, disabled: !live || null });
  const replayRow = h("label", { class: "row small", hidden: (!demo || state.mode === "audio") || null }, replayBox,
    live ? " Replay the scripted demo output (DEMO / REPLAY) instead of a live evaluation"
         : " DEMO / REPLAY: scripted model output replayed through the real rule engine (live evaluation is not configured)");
  const btn = h("button", { class: "btn btn-primary", type: "button" }, "Evaluate");

  btn.addEventListener("click", async () => {
    const fd = new FormData();
    fd.append("mode", state.mode);
    if (callName.value.trim()) fd.append("call_name", callName.value.trim());
    if (state.mode !== "audio") {  // audio-only sends the audio alone
      fd.append("transcript", transcriptBox.value);
      if (tFile.files[0]) fd.append("transcript_file", tFile.files[0]);
      if (state.demoId) { fd.append("demo_id", state.demoId); fd.append("replay", replayBox.checked ? "true" : "false"); }
    }
    if (state.mode !== "transcript" && aFile.files[0]) fd.append("audio_file", aFile.files[0]);
    btn.disabled = true; out.replaceChildren(h("p", { class: "spinner-line" },
      live && !(state.demoId && replayBox.checked) ? "Evaluating live (two model calls; this can take up to a minute)…" : "Evaluating…"));
    try {
      const res = await api("/api/evaluate", { method: "POST", body: fd });
      state.lastResult = res;
      location.hash = "#/result/" + res.evaluation_id;
    } catch (err) { out.replaceChildren(errorNotice(err)); }
    finally { btn.disabled = false; }
  });

  const modeButtons = h("div", { class: "segmented", role: "tablist", "aria-label": "Input mode" },
    modes.map(m => h("button", { type: "button", role: "tab", class: m.id === state.mode ? "active" : "",
      "aria-selected": m.id === state.mode ? "true" : "false",
      onclick: () => { state.mode = m.id; renderEvaluate(); } }, m.label)));

  const showTranscript = state.mode !== "audio";
  const showAudio = state.mode !== "transcript";
  const form = h("section", { class: "panel" },
    h("h1", {}, "Evaluate a call"),
    h("p", { class: "muted" }, "Choose how the call reaches the judge, supply it, and evaluate. The verdict holds within the available evidence."),
    modeButtons,
    h("div", { class: "notice " + (modeInfo.supported ? "info" : "warn") }, modeInfo.note || ""),
    h("label", { class: "field", for: "call-name" }, "Call name (optional)"), callName,
    showTranscript ? [h("label", { class: "field", for: "transcript" }, "Transcript"), transcriptBox,
      h("div", { class: "row small", style: "margin-top:8px" }, h("span", { class: "muted" }, "or upload .txt / .json:"), tFile)] : null,
    showAudio ? [h("label", { class: "field", for: "audio-file" }, state.mode === "audio" ? "Audio" : "Audio file"), aFile,
      h("div", { class: "small muted" }, "wav, mp3 or m4a, up to " + ((cfg && cfg.limits.audio_mb) || 25) + " MB. The audio is fingerprinted in memory and not stored.")] : null,
    replayRow,
    h("div", { class: "row", style: "margin-top:14px" }, btn,
      h("span", { class: "small muted" }, live ? "LIVE EVALUATION available: " + cfg.evaluator.provider + " · " + cfg.evaluator.model_id
        : "Live evaluation is not configured on this server. Demo calls replay; other calls get the front-end checks only.")),
    out);

  const demos = h("section", { class: "panel" },
    h("h2", {}, "Demo calls"),
    h("p", { class: "small muted" }, "Synthetic calls (fictional names and amounts). Without a live evaluator they run as DEMO / REPLAY: scripted model output through the real rule engine."),
    h("div", { class: "demo-list" }, state.demos.map(d => h("button", {
      type: "button", class: "demo" + (d.id === state.demoId ? " selected" : ""),
      onclick: () => { state.demoId = d.id; state.mode = "transcript"; renderEvaluate(); }
    }, h("b", {}, d.title), h("span", {}, d.scenario)))));

  const help = h("section", { class: "panel" },
    h("h2", {}, "Transcript format"),
    h("ul", { class: "list-plain small" },
      h("li", {}, "One turn per line: AGENT: … / BORROWER: … (CUSTOMER: also works)."),
      h("li", {}, "Optional header lines first: # call_start_ts: 2026-09-28T14:05:00+05:30 (enables the calling-hours check)."),
      h("li", {}, "Optional timestamps: [00:03.2-00:07.9] AGENT: …, all turns or none."),
      h("li", {}, "Mark unclear speech with [inaudible] or [crosstalk]; those spans are not judged as if clear.")));

  view.replaceChildren(h("div", { class: "grid" }, h("div", {}, form), h("div", {}, demos, help)));
}

/* ------------------------------------------------------------------ Screen 2: Result */
function sourceBanner(r) {
  const sub = { LIVE: r.evaluator.model_called ? "Judged now by " + r.evaluator.provider + " · " + r.evaluator.model + " with evaluator B " + r.evaluator.system_version
                                               : "Decided now by the deterministic front end; no model call was needed.",
    DEMO_REPLAY: "Scripted model output replayed through the real rule engine. Not a live evaluation and not a measurement.",
    UNAVAILABLE: "The evaluator did not run on this server." }[r.source];
  return h("div", { class: "source-banner " + r.source }, r.source_label, h("span", {}, sub));
}

function evidenceQuote(e) {
  if (e.header_field) return h("div", { class: "quote" }, "Transcript header: " + e.header_field);
  return h("div", { class: "quote" },
    h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); flashTurn(e.turn); } }, "turn " + e.turn),
    h("b", {}, (e.role || "") + ": "), "“" + (e.quote || "") + "”");
}
function flashTurn(n) {
  const el = document.getElementById("turn-" + n);
  if (!el) return;
  el.scrollIntoView({ block: "center" });
  el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1200);
}

function renderResult(r) {
  setActive("library");
  const main = [];
  main.push(sourceBanner(r));
  const vcode = r.verdict ? r.verdict.code : null;
  const hero = h("section", { class: "panel verdict " + (vcode || "none") },
    h("div", { class: "small muted" }, r.call.name + " · " + r.call.mode_label + " · " + r.call.n_turns + " turns"),
    h("div", { class: "verdict-label" }, r.verdict ? r.verdict.label : "No verdict",
      r.verdict && r.verdict.critical_status ? h("small", {}, " " + r.verdict.critical_status) : null,
      r.verdict && r.verdict.code ? h("small", {}, " — within available evidence") : null),
    h("div", { class: "key-finding" }, h("b", {}, r.key_finding.title),
      r.key_finding.explanation ? h("div", { class: "small" }, r.key_finding.explanation) : null),
    r.tags ? h("div", { class: "chips" },
      badge(r.tags.dangerous_win === "NONE" ? "No Dangerous Win" : "Dangerous Win: " + r.tags.dangerous_win, r.tags.dangerous_win === "NONE" ? "plain" : "tag-on"),
      badge(r.tags.clean_loss ? "Clean Loss" : "Not a Clean Loss", r.tags.clean_loss ? "tag-cl" : "plain"),
      r.uncertainty ? badge("Evaluability: " + r.uncertainty.evaluability, "plain") : null,
      r.routing && r.routing.tier ? badge("Routing tier " + r.routing.tier, "plain") : null) : null);
  main.push(hero);

  // Why
  const why = h("section", { class: "panel" }, h("h3", {}, "Why"));
  if (r.status === "NOT_RUN" || r.status === "EVALUATION_FAILED") {
    why.append(h("div", { class: "notice " + (r.status === "EVALUATION_FAILED" ? "err" : "warn") }, r.key_finding.explanation));
    if (r.frontend) why.append(h("p", { class: "small" }, "Front-end checks that did run: evaluability " + r.frontend.evaluability +
      (r.frontend.reason_text ? " (" + r.frontend.reason_text + ")" : "") + "; pre-checks " +
      Object.entries(r.frontend.prechecks).map(([k, v]) => k + " " + v).join(", ") + "."));
  } else if (!r.findings.length) {
    why.append(h("p", {}, "No gate fired and no finding was asserted."));
  }
  for (const f of r.findings || []) {
    why.append(h("div", { class: "finding" },
      h("div", { class: "finding-head" }, h("span", { class: "code" }, f.code), h("b", {}, f.name || ""),
        badge(f.severity, "sev-" + f.severity), badge(f.status, "plain")),
      h("dl", { class: "finding-meta" },
        h("div", {}, h("dt", {}, "Dimension"), h("dd", {}, f.dimension || "-")),
        h("div", {}, h("dt", {}, "Confidence"), h("dd", {}, f.confidence || "-")),
        h("div", {}, h("dt", {}, "Attribution"), h("dd", {}, f.attribution ? f.attribution.label : "-")),
        h("div", {}, h("dt", {}, "Action type"), h("dd", {}, f.action_type || "-"))),
      f.explanation ? h("div", { class: "small muted" }, h("b", {}, "Rule: "), f.explanation) : null,
      f.evidence_unverified ? h("div", { class: "notice warn" }, "The cited evidence could not be verified against the transcript.") : null,
      (f.evidence || []).map(evidenceQuote)));
  }
  main.push(why);

  // Evidence
  const tr = h("div", { class: "transcript" }, (r.transcript || []).map(t => h("div", {
    id: "turn-" + t.turn, class: "turn" + (t.cited ? " cited" : "") + (t.unreliable ? " unreliable" : "") },
    h("span", { class: "n" }, t.turn), h("span", { class: "r" }, t.role), h("span", { class: "t" }, t.text))));
  const evid = h("section", { class: "panel" }, h("h3", {}, "Evidence"),
    h("p", { class: "small muted" }, "Highlighted turns are cited by a finding or an unverified commitment."), tr);
  if (r.unverified_commitments && r.unverified_commitments.length) {
    evid.append(h("h3", { style: "margin-top:14px" }, "Unverified agent commitments"),
      h("p", { class: "small muted" }, "Things the agent promised to do. Whether they were done needs systems outside the call (EXE-03, out of scope), so they are listed, not judged."),
      ...r.unverified_commitments.map(evidenceQuote));
  }
  main.push(evid);

  // Confidence / uncertainty
  if (r.uncertainty) {
    const u = r.uncertainty;
    main.push(h("section", { class: "panel" }, h("h3", {}, "Confidence and uncertainty"),
      h("div", { class: "kv" },
        h("div", {}, "Evaluability"), h("div", {}, h("b", {}, u.evaluability), u.reason_text ? " - " + u.reason_text : ""),
        h("div", {}, "All in-scope checks decided"), h("div", {}, u.within_scope_complete ? "yes" : "no"),
        h("div", {}, "Confidence source"), h("div", {}, u.confidence_source || "-")),
      h("p", { class: "small muted" }, u.note),
      h("div", { class: "grid-2" },
        h("div", {}, h("b", {}, "INCONCLUSIVE (" + u.inconclusive.length + ")"),
          u.inconclusive.length ? h("ul", { class: "list-plain" }, u.inconclusive.map(c => h("li", {}, h("span", { class: "mono" }, c.code), " " + (c.name || "") + " - " + c.reasons))) : h("p", { class: "small muted" }, "none")),
        h("div", {}, h("b", {}, "OUT OF SCOPE (" + u.out_of_scope.length + ")"),
          h("p", { class: "small muted" }, "Not judged from a call alone: they need data outside the call or a capability this input mode lacks."),
          u.out_of_scope.length ? h("details", {}, h("summary", { class: "small" }, "Show the " + u.out_of_scope.length + " checks"),
            h("ul", { class: "list-plain small" }, u.out_of_scope.map(c => h("li", {}, h("span", { class: "mono" }, c.code), " " + (c.name || "") + " - " + c.reason)))) : null))));
  }

  // Attribution
  if (r.findings && r.findings.length) {
    main.push(h("section", { class: "panel" }, h("h3", {}, "Attribution"),
      h("p", { class: "small muted" }, "Who or what each finding is attributed to, by rubric rule. The model's reasoning is not shown."),
      h("ul", { class: "list-plain" }, r.findings.map(f => h("li", {}, h("span", { class: "mono" }, f.code), " → ",
        h("b", {}, f.attribution ? f.attribution.label : "-"),
        f.attribution && f.attribution.secondary ? " (secondary: " + f.attribution.secondary + ")" : "",
        f.attribution ? " · basis " + f.attribution.basis : "")))));
  }
  if (r.tags) {
    main.push(h("section", { class: "panel" }, h("h3", {}, "Outcome tags"),
      h("p", {}, r.tags.dangerous_win_text), h("p", {}, r.tags.clean_loss_text),
      r.outcome ? h("p", { class: "small muted" }, "Outcome: " + (r.outcome.dispositions || []).join(", ") +
        " · positive " + r.outcome.positive + (r.outcome.firmness ? " · firmness " + r.outcome.firmness : "") +
        (r.outcome.outcome_attribution ? " · driven by " + r.outcome.outcome_attribution : "")) : null));
  }

  // Action / routing
  main.push(h("section", { class: "panel" }, h("h3", {}, "Action and routing"),
    h("p", {}, h("b", {}, r.action)),
    r.routing ? h("p", { class: "small muted" }, r.routing.label) : null,
    r.record ? h("button", { class: "btn btn-ghost", type: "button", onclick: () => downloadRecord(r) }, "Download evaluation record (JSON)") : null));

  const side = [evaluatorPanel(r), profilePanel()];
  view.replaceChildren(h("div", { class: "grid" }, h("div", {}, main), h("div", {}, side)));
}

function downloadRecord(r) {
  const blob = new Blob([JSON.stringify(r.record, null, 2)], { type: "application/json" });
  const a = h("a", { href: URL.createObjectURL(blob), download: r.evaluation_id + ".json" });
  document.body.append(a); a.click(); a.remove();
}

function evaluatorPanel(r) {
  const e = r.evaluator, u = r.usage || {};
  return h("section", { class: "panel" }, h("h3", {}, "Evaluator"),
    h("div", { class: "kv" },
      h("div", {}, "Source"), h("div", {}, badge(r.source_label, r.source)),
      h("div", {}, "System"), h("div", {}, e.system + " " + e.system_version),
      h("div", {}, "Provider"), h("div", {}, e.provider),
      h("div", {}, "Model"), h("div", { class: "mono" }, e.model),
      h("div", {}, "Prompt"), h("div", { class: "mono" }, e.prompt_version),
      h("div", {}, "Engine"), h("div", { class: "mono" }, e.engine_version),
      h("div", {}, "Temperature"), h("div", {}, String(e.temperature)),
      h("div", {}, "Model calls"), h("div", {}, String(u.llm_calls || 0) + (u.schema_retries ? " (" + u.schema_retries + " schema retry)" : "")),
      h("div", {}, "Tokens in / out"), h("div", {}, (u.input_tokens || 0) + " / " + (u.output_tokens || 0)),
      h("div", {}, "Latency"), h("div", {}, (u.latency_s || 0) + " s"),
      h("div", {}, "Evaluated at"), h("div", { class: "small" }, r.created_at)));
}

function profilePanel() {
  const p = state.config && state.config.profile;
  if (!p) return null;
  return h("section", { class: "panel" }, h("h3", {}, "Evaluation profile (read-only)"),
    h("div", { class: "kv" },
      h("div", {}, "Profile"), h("div", { class: "mono" }, p.profile_id + " " + p.profile_version),
      h("div", {}, "Rubric"), h("div", { class: "mono" }, p.rubric_version),
      h("div", {}, "Contract"), h("div", { class: "mono" }, p.contract_version),
      p.settings.map(s => [h("div", {}, s.name), h("div", {}, s.value)])),
    h("p", { class: "small muted" }, p.disclaimer),
    h("details", {}, h("summary", { class: "small" }, "Hard gates (" + p.gates.length + ")"),
      h("ul", { class: "list-plain small" }, p.gates.map(g => h("li", {}, h("span", { class: "mono" }, g.id), " " + g.name)))),
    h("p", { class: "small muted" }, p.pending_signoff.count + " profile / rubric values await human sign-off; they are never defaulted."));
}

/* ------------------------------------------------------------------ Screen 3: Call Library */
async function renderLibrary() {
  setActive("library");
  view.replaceChildren(h("p", { class: "spinner-line" }, "Loading…"));
  let rows;
  try { rows = await api("/api/calls"); } catch (err) { view.replaceChildren(errorNotice(err)); return; }
  const body = rows.map(r => h("tr", { class: "click", onclick: () => { location.hash = "#/result/" + r.evaluation_id; } },
    h("td", {}, h("b", {}, r.name)),
    h("td", {}, r.modality),
    h("td", { class: "v-" + (r.verdict_code || "null") }, r.verdict || (r.status === "EVALUATION_FAILED" ? "Evaluation Failed" : "No verdict")),
    h("td", {}, r.primary_finding || "-"),
    h("td", {}, { OK: "Evaluated", EVALUATION_FAILED: "Failed", NOT_RUN: "Not run" }[r.status] || r.status,
      r.evaluability ? h("div", { class: "small muted" }, "Evaluability: " + r.evaluability) : null),
    h("td", {}, badge(r.source_label, r.source))));
  view.replaceChildren(h("section", { class: "panel" },
    h("h1", {}, "Call library"),
    h("p", { class: "muted small" }, "Demo calls and calls evaluated on this server since it started (kept in memory, never persisted). DEV / demo only: no production or holdout calls."),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["Call", "Modality", "Verdict", "Primary finding", "Status", "Source"].map(c => h("th", {}, c)))),
      h("tbody", {}, body)))));
}

/* ------------------------------------------------------------------ Screen 4: Evaluator Reliability */
async function renderReliability() {
  setActive("reliability");
  view.replaceChildren(h("p", { class: "spinner-line" }, "Loading…"));
  let r;
  try { r = await api("/api/reliability"); } catch (err) { view.replaceChildren(errorNotice(err)); return; }
  const blocks = [
    h("section", { class: "panel" },
      h("div", { class: "big-label" }, r.label),
      h("div", { class: "pending", style: "margin-top:6px" }, r.final_validation),
      h("p", { class: "small muted" }, "These are engineering measurements on DEV drafts against the frozen design intent. They are not gold-scored, not holdout results and not reliability evidence.")),
    h("section", { class: "panel" }, h("h3", {}, "Evaluator under measurement"),
      h("div", { class: "kv" },
        h("div", {}, "Evaluator"), h("div", {}, r.evaluator.system + " · version " + r.evaluator.system_version),
        h("div", {}, "Provider / model"), h("div", {}, r.evaluator.provider + " · " + r.evaluator.model),
        h("div", {}, "Prompt version"), h("div", { class: "mono" }, r.evaluator.prompt_version),
        h("div", {}, "Engine"), h("div", { class: "mono" }, r.evaluator.engine_version),
        h("div", {}, "Live on this server"), h("div", {}, r.evaluator.live_available ? "yes" : "no (GEMINI_API_KEY not set)"),
        h("div", {}, "Gold"), h("div", {}, r.gold),
        h("div", {}, "Holdout"), h("div", {}, r.holdout),
        h("div", {}, "Red team"), h("div", {}, r.red_team))),
  ];
  if (r.status !== "OK") {
    blocks.push(h("div", { class: "notice warn" }, r.message));
    view.replaceChildren(...blocks); return;
  }
  blocks.push(h("section", { class: "panel" }, h("h3", {}, "DEV benchmark"),
    h("div", { class: "kv" },
      h("div", {}, "Split"), h("div", {}, r.benchmark.split),
      h("div", {}, "Calls measured"), h("div", {}, r.benchmark.items_executed + " (" + r.benchmark.items_excluded + " excluded: snippets and a tuning-only copy)"),
      h("div", {}, "Modality"), h("div", {}, r.run.unit_mode + " · " + r.run.asr_mode),
      h("div", {}, "Reference"), h("div", {}, r.benchmark.reference),
      h("div", {}, "Run"), h("div", { class: "mono" }, r.run.run_id + " · commit " + r.run.git_commit + " · reps " + r.run.repetitions))));
  const metricKeys = [["verdict_accuracy", "Verdict accuracy"], ["critical_recall", "Critical recall"],
    ["must_not_fire_precision", "Must-not-fire precision"], ["pair_accuracy", "Pair accuracy"],
    ["defect_precision", "Defect precision"], ["defect_recall", "Defect recall"],
    ["evidence_faithfulness", "Evidence faithfulness"], ["attribution_agreement", "Attribution agreement"],
    ["dangerous_win_agreement", "Dangerous Win agreement"], ["clean_loss_agreement", "Clean Loss agreement"]];
  const names = Object.keys(r.systems);
  const rows = metricKeys.map(([k, label]) => h("tr", {}, h("td", {}, label), names.map(n => {
    const s = r.systems[n];
    return h("td", {}, s.status !== "EXECUTED" ? "-" : pct((s.metrics || {})[k]));
  })));
  rows.push(h("tr", {}, h("td", {}, "Abstention"), names.map(n => {
    const a = r.systems[n].abstention;
    return h("td", {}, a ? "EF " + a.evaluation_failed + "/" + a.records + " · NE " + a.not_evaluable + " · PARTIAL " + a.partial : "-");
  })));
  rows.push(h("tr", {}, h("td", {}, "Status"), names.map(n => {
    const s = r.systems[n];
    return h("td", {}, s.status === "EXECUTED" ? "EXECUTED" : h("span", { class: "pending" }, "NOT EXECUTED"),
      s.reason ? h("div", { class: "small muted" }, s.reason) : null);
  })));
  blocks.push(h("section", { class: "panel" }, h("h3", {}, "K0 / A / A+ / B on DEV"),
    h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Metric"), names.map(n => h("th", {}, n)))),
      h("tbody", {}, rows)))));
  const cs = r.reproducibility;
  blocks.push(h("section", { class: "panel" }, h("h3", {}, "Reproducibility"),
    cs ? h("p", {}, "Run " + cs.run_id + ": " + cs.repetitions + " repetitions under the same settings; overall stable: " + cs.stable + ".")
       : h("p", { class: "muted" }, "Not run."),
    h("p", { class: "small muted" }, "Front end: evaluability agreement " + pct(r.frontend.evaluability_agreement) + " · G7 agreement " + pct(r.frontend.g7_agreement) + ".")));
  blocks.push(h("section", { class: "panel" }, h("h3", {}, "UNMEASURED"),
    r.unmeasured.length ? h("ul", { class: "list-plain" }, r.unmeasured.map(u => h("li", {}, u))) : h("p", { class: "muted" }, "none"),
    h("p", { class: "small muted" }, "A metric is UNMEASURED when the run gives it no support. It is never filled in.")));
  view.replaceChildren(...blocks);
}

/* ------------------------------------------------------------------ routing */
async function route() {
  const hash = location.hash || "#/evaluate";
  const parts = hash.slice(2).split("/");
  view.focus({ preventScroll: true });
  if (parts[0] === "result" && parts[1]) {
    if (state.lastResult && state.lastResult.evaluation_id === parts[1]) { renderResult(state.lastResult); return; }
    try { renderResult(await api("/api/calls/" + encodeURIComponent(parts[1]))); }
    catch (err) { view.replaceChildren(errorNotice(err)); }
    return;
  }
  if (parts[0] === "library") return renderLibrary();
  if (parts[0] === "reliability") return renderReliability();
  return renderEvaluate();
}

async function boot() {
  try {
    const [cfg, demos] = await Promise.all([api("/api/config"), api("/api/demo-calls")]);
    state.config = cfg; state.demos = demos;
    const ls = document.getElementById("live-status");
    ls.textContent = cfg.live_available ? "LIVE EVALUATION: " + cfg.evaluator.model_id : "DEMO / REPLAY only (live evaluator not configured)";
    ls.classList.toggle("on", !!cfg.live_available);
    document.getElementById("footer-version").textContent = "v" + cfg.product.version + " · evaluator B " + cfg.evaluator.system_version + " · " + cfg.evaluator.prompt_version;
  } catch (err) { view.replaceChildren(errorNotice(err)); return; }
  window.addEventListener("hashchange", route);
  route();
}
boot();

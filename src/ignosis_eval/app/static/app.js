/* Ignosis Call Quality Judge: single-page client for AI Quality Reviewers.
   It talks only to this server's /api and never holds a provider key. Every dynamic string is inserted with
   textContent (no HTML injection). Journey: 1 Choose a call -> 2 Choose how it is provided -> 3 Evaluate ->
   4 Review verdict and evidence -> 5 Explore reliability. Audio-only results are EXPERIMENTAL and always labelled. */
"use strict";

const state = {
  config: null, demos: [], lastResult: null, origin: "evaluate", busy: false, selected: 0,
  mode: "transcript",
  form: { transcript: "", callName: "", demoId: null, transcriptFile: null, audio: null, live: false },
  library: { verdict: "all", source: "all", q: "" },
};
const view = document.getElementById("view");

/* ------------------------------------------------------------------ copy (reviewer language) */
const VERDICT = {
  CRITICAL_FAIL: { label: "Critical Fail", cls: "crit", meaning: "A hard rule was broken. Escalate this call." },
  NEEDS_ATTENTION: { label: "Needs Attention", cls: "warn", meaning: "No hard rule was broken, but the agent made at least one mistake that needs correcting." },
  MEETS_BAR: { label: "Meets Bar", cls: "ok", meaning: "No defects were found in everything that could be checked." },
  NOT_EVALUABLE: { label: "Not Evaluable", cls: "ne", meaning: "The call cannot be judged, for example a voicemail or unclear speakers." },
};
const EVALUABILITY = {
  EVALUABLE: { label: "Fully evaluable", cls: "ok", text: "Every check that applies to this call could be decided." },
  PARTIAL: { label: "Partially evaluable", cls: "warn", text: "Some checks could not be decided (listed below). The verdict covers only what could be checked." },
  NOT_EVALUABLE: { label: "Not evaluable", cls: "ne", text: "The call itself could not be assessed, so no conduct judgement was made." },
};
const DW_HELP = "Positive outcome (for example a promise to pay), but the agent broke a hard rule, or made a major mistake before the commitment, to get it. The win carries risk.";
const CL_HELP = "No positive outcome, but the agent behaved to standard and the result was driven by the customer or by policy (for example a dispute correctly escalated). Not an agent failure.";
const SEVERITY = { CRITICAL: "Critical", MAJOR: "Major", MINOR: "Minor", INFORMATIONAL: "Info" };
const MODE_CHIP = { audio: ["EXPERIMENTAL AUDIO", "exp"], audio_transcript: ["Strongest", "ok"] };
const SYSTEM_NAMES = { K0: "Keyword baseline", A: "Single-pass model judge", "A+": "Model judge + rule checks", B: "Structured evaluator (used in this app)" };
const METRICS = [
  ["verdict_accuracy", "Verdict accuracy", "Calls where the verdict matched the intended verdict."],
  ["critical_recall", "Critical-failure recall", "Intended hard-rule failures that were caught."],
  ["must_not_fire_precision", "No false alarm on traps", "Trap calls where a hard rule was correctly not flagged."],
  ["pair_accuracy", "Paired-call accuracy", "Near-identical call pairs (one defective, one clean) told apart."],
  ["defect_precision", "Defect precision", "Reported defects that were intended."],
  ["defect_recall", "Defect recall", "Intended defects that were reported."],
  ["evidence_faithfulness", "Evidence faithfulness", "Cited evidence that really appears in the transcript."],
  ["attribution_agreement", "Attribution agreement", "Defects attributed to the right cause."],
  ["dangerous_win_agreement", "Dangerous Win agreement", "Dangerous Win tag matched the intended tag."],
  ["clean_loss_agreement", "Clean Loss agreement", "Clean Loss tag matched the intended tag."],
];

/* ------------------------------------------------------------------ helpers */
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
  if (!res.ok) throw (body && body.error) || { code: "HTTP_" + res.status, message: "The server could not complete the request (" + res.status + ").", pathway: [] };
  return body;
}
function store(key, value) {
  try { if (value === undefined) return window.localStorage.getItem(key); window.localStorage.setItem(key, value); } catch (e) { return null; }
  return null;
}
function chip(text, cls) { return h("span", { class: "chip " + (cls || "") }, text); }
function sourceChip(source, label) { return h("span", { class: "chip src-" + source }, label); }
function pct(m) {
  if (!m || m.value === null || m.value === undefined || m.value === "UNMEASURED") return m && m.den ? m.num + " of " + m.den : "UNMEASURED";
  return Math.round(m.value * 100) + "%  (" + m.num + " of " + m.den + ")";
}
function setNav(route) {
  document.querySelectorAll(".nav a").forEach(a => {
    const on = a.dataset.route === route;
    a.classList.toggle("active", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
}
function page(...children) { view.replaceChildren(...children.flat(Infinity).filter(Boolean)); }
function notice(kind, title, lines, actions) {
  return h("div", { class: "notice " + kind, role: kind === "err" ? "alert" : "status" },
    title ? h("strong", {}, title) : null,
    (lines || []).length ? h("ul", {}, lines.map(l => h("li", {}, l))) : null,
    actions ? h("div", { class: "row gap-s mt-s" }, actions) : null);
}
const STEPS = [["Choose a call", "Call"], ["Choose how it's provided", "Input"], ["Evaluate", "Evaluate"],
  ["Review verdict & evidence", "Review"], ["Explore reliability", "Explore"]];
function stepper(current, busy) {
  return h("ol", { class: "stepper", id: "stepper", "aria-label": "Your progress" }, STEPS.map(([long, short], i) => h("li", {
    class: i < current ? "done" : i === current ? "current" + (busy ? " busy" : "") : "",
    "aria-current": i === current ? "step" : null,
  }, h("span", { class: "step-num", "aria-hidden": "true" }, i < current ? "✓" : String(i + 1)),
    h("span", { class: "step-long" }, long), h("span", { class: "step-short", "aria-hidden": "true" }, short),
    i < current ? h("span", { class: "sr-only" }, " (done)") : null)));
}
function evaluateStep() { const f = state.form; return f.demoId || f.transcript.trim() || f.transcriptFile || f.audio ? 1 : 0; }
function setStepper(current, busy) {
  const el = document.getElementById("stepper");
  if (el) el.replaceWith(stepper(current, busy));
}

/* ------------------------------------------------------------------ how it works (dismissible) */
function helpPanel() {
  return h("section", { class: "help-panel", id: "help-panel", "aria-labelledby": "help-title" },
    h("div", { class: "help-head" }, h("h2", { id: "help-title" }, "How it works"),
      h("button", { type: "button", class: "btn btn-quiet", onclick: () => toggleHelp(false) }, "Got it, hide this")),
    h("ol", { class: "help-steps" },
      h("li", {}, h("strong", {}, "Choose a call. "), "Start with a fictional demo call, or bring your own."),
      h("li", {}, h("strong", {}, "Choose how it's provided. "), "Transcript, Audio (experimental) or Audio + Transcript."),
      h("li", {}, h("strong", {}, "Evaluate. "), "The judge checks the agent against the collections rules."),
      h("li", {}, h("strong", {}, "Review. "), "Verdict first; select a finding to see its transcript lines."),
      h("li", {}, h("strong", {}, "Explore. "), "Past calls in the Call library; accuracy under Evaluator reliability.")),
    h("p", { class: "small muted help-foot" }, "Results are labelled LIVE EVALUATION (judged now by Google Gemini), DEMO / REPLAY (a fictional call with a recorded evaluation) or EXPERIMENTAL AUDIO (Gemini listened to a recording; not independently calibrated)."));
}
function toggleHelp(show) {
  const slot = document.getElementById("help-slot");
  const btn = document.getElementById("help-toggle");
  const open = show === undefined ? !slot.firstChild : show;
  slot.replaceChildren(...(open ? [helpPanel()] : []));
  btn.setAttribute("aria-expanded", open ? "true" : "false");
  if (!open) store("ignosis.help.dismissed", "1");
}

/* ------------------------------------------------------------------ Screen 1: Evaluate a call */
function renderEvaluate() {
  setNav("evaluate");
  const cfg = state.config, live = cfg.live_available, f = state.form;
  const demo = state.demos.find(d => d.id === f.demoId);
  const sample = state.demos.find(d => d.audio);
  const audioMode = state.mode === "audio";
  const sampleAudio = !!(f.audio && f.audio.sample);

  const demoCards = state.demos.map(d => h("article", { class: "demo-card" },
    h("div", { class: "row gap-s wrap" }, chip(d.illustrates, "tag-" + tagClass(d.illustrates)), d.audio ? chip("Includes a sample recording", "plain") : null),
    h("h3", {}, d.title),
    h("p", { class: "small muted clamp", title: d.scenario }, d.scenario),
    h("div", { class: "row gap-s wrap mt-auto" },
      h("button", { type: "button", class: "btn btn-primary btn-sm", "data-demo": d.id,
        onclick: (e) => runDemo(d, e.currentTarget) }, "View evaluation"),
      h("button", { type: "button", class: "btn btn-link btn-sm", onclick: () => loadDemo(d, true) }, "Open in step 2"))));

  const modeCards = h("fieldset", { class: "mode-group" }, h("legend", { class: "sr-only" }, "How is the call provided?"),
    h("div", { class: "mode-options" }, (cfg.modes || []).map(m => h("label", { class: "mode-option" + (state.mode === m.id ? " selected" : "") + (m.experimental ? " experimental" : "") },
      h("input", { type: "radio", name: "mode", value: m.id, checked: state.mode === m.id || null,
        onchange: () => { switchMode(m.id); document.querySelector('input[name="mode"]:checked').focus(); } }),
      h("span", { class: "mode-title" }, m.label, MODE_CHIP[m.id] ? chip(MODE_CHIP[m.id][0], MODE_CHIP[m.id][1]) : null),
      h("span", { class: "mode-sub" }, m.summary)))));
  const modeInfo = (cfg.modes || []).find(m => m.id === state.mode) || {};

  const ta = h("textarea", { id: "transcript", spellcheck: "false", "aria-describedby": "transcript-help",
    placeholder: "AGENT: Namaste, main Priya, Acme Finserv se bol rahi hoon. Kya meri baat Rohan ji se ho rahi hai?\nBORROWER: Haan ji, boliye.\nAGENT: …",
    oninput: (e) => { f.transcript = e.target.value; setStepper(evaluateStep()); if (demo && e.target.value.trim() !== demo.transcript.trim()) { f.demoId = null; renderEvaluate(); focusEnd("transcript"); } } });
  ta.value = f.transcript;
  const tFile = h("input", { type: "file", id: "transcript-file", accept: ".txt,.json", class: "file-input",
    onchange: (e) => { f.transcriptFile = e.target.files[0] || null; f.demoId = null; renderEvaluate(); } });
  const formats = audioMode ? cfg.limits.audio_only_formats : cfg.limits.audio_formats;
  const aFile = h("input", { type: "file", id: "audio-file", accept: formats.map(x => "." + x).join(","), class: "file-input",
    onchange: (e) => { const file = e.target.files[0]; if (file) setAudio({ file, name: file.name, url: URL.createObjectURL(file), sample: false }); } });

  const transcriptField = audioMode ? null : h("div", { class: "field" },
    h("label", { for: "transcript", class: "field-label" }, "Transcript"),
    ta,
    h("div", { class: "row gap-s wrap small mt-s", id: "transcript-help" },
      tFile, h("label", { class: "btn btn-secondary btn-sm file-btn", for: "transcript-file" }, "Upload .txt or .json"),
      f.transcriptFile ? h("span", {}, "Selected: ", h("strong", {}, f.transcriptFile.name), " ",
        h("button", { type: "button", class: "btn btn-link btn-sm", onclick: () => { f.transcriptFile = null; renderEvaluate(); } }, "Remove")) : null,
      h("details", { class: "inline-details" }, h("summary", {}, "Transcript format"),
        h("ul", { class: "small" },
          h("li", {}, "One turn per line, starting AGENT: or BORROWER: (CUSTOMER: also works)."),
          h("li", {}, "Optional first line: # call_start_ts: 2026-09-28T14:05:00+05:30 (enables the calling-hours check)."),
          h("li", {}, "Mark unclear speech with [inaudible]; those lines are not judged as if clear.")))));

  const audioHelp = audioMode
    ? formats.join(" or ") + ", up to " + cfg.limits.audio_only_mb + " MB. Sent to Google Gemini for this evaluation only (without its file name) and not stored."
    : formats.join(", ") + ", up to " + cfg.limits.audio_mb + " MB. Fingerprinted in memory, attached to the result and not stored; it never overrides the transcript.";
  const audioField = state.mode === "transcript" ? null : h("div", { class: "field" },
    h("span", { class: "field-label", id: "audio-label" }, "Call recording"),
    h("div", { class: "row gap-s wrap", role: "group", "aria-labelledby": "audio-label" },
      aFile, h("label", { class: "btn btn-secondary btn-sm file-btn", for: "audio-file" }, f.audio ? "Choose a different recording" : "Choose a recording"),
      sample ? h("button", { type: "button", class: "btn btn-secondary btn-sm", onclick: () => useSample(sample) }, "Use the sample recording") : null),
    f.audio ? h("div", { class: "audio-preview" },
      h("div", { class: "small" }, h("strong", {}, f.audio.name), f.audio.sample ? "  ·  sample recording (synthetic voices, fictional call; DEMO)" : "",
        " ", h("button", { type: "button", class: "btn btn-link btn-sm", onclick: () => { f.audio = null; renderEvaluate(); } }, "Remove")),
      h("audio", { controls: true, preload: "none", src: f.audio.url, "aria-label": "Play the selected recording" })) : null,
    h("p", { class: "small muted" }, audioHelp));

  const offerMethod = live && (audioMode ? sampleAudio : !!demo);
  const methodField = offerMethod ? h("fieldset", { class: "method-group" }, h("legend", {}, audioMode ? "How should the sample recording be evaluated?" : "How should this demo call be evaluated?"),
    h("label", { class: "radio-line" }, h("input", { type: "radio", name: "method", checked: !f.live || null, onchange: () => { f.live = false; renderEvaluate(); } }),
      " Recorded demo evaluation (instant, DEMO / REPLAY)"),
    h("label", { class: "radio-line" }, h("input", { type: "radio", name: "method", checked: f.live || null, onchange: () => { f.live = true; renderEvaluate(); } }),
      audioMode ? " Live: Gemini listens to the recording (" + cfg.evaluator.model_id + ", experimental, up to a minute or two)"
        : " Live evaluation with " + cfg.evaluator.model_id + " (up to a minute)")) : null;

  const expectation = audioMode
    ? (!f.audio ? "Choose a recording, or use the sample recording."
      : sampleAudio && (!live || !f.live) ? "Shows the recorded demo evaluation of the sample recording (DEMO / REPLAY, experimental audio)."
      : live ? "EXPERIMENTAL AUDIO: Gemini (" + cfg.evaluator.model_id + ") listens to the recording, then the judge checks each rule. Usually 20 to 90 seconds."
      : "Live evaluation isn't configured on this server, so an uploaded recording can't be listened to: you will get no verdict. The sample recording still shows its recorded demo evaluation.")
    : demo && !(live && f.live) ? "Shows the recorded evaluation of this fictional demo call (DEMO / REPLAY)."
    : live ? "Evaluated live by Google Gemini (" + cfg.evaluator.model_id + "). This usually takes 10 to 60 seconds."
    : "Live evaluation isn't configured on this server: your call gets the automatic pre-checks only, with no verdict. Demo calls still show their recorded evaluations.";

  page(
    stepper(evaluateStep()),
    h("header", { class: "page-head" },
      h("h1", {}, "Evaluate a Voice AI call"),
      h("p", { class: "lead" }, "Choose a collections call, say how it is provided, and the judge tells you whether the AI agent behaved correctly."),
      h("div", { class: "row gap-s wrap cta-row" },
        h("button", { type: "button", class: "btn btn-primary", onclick: () => jump("demos") }, "Try a demo call"),
        h("button", { type: "button", class: "btn btn-secondary", onclick: () => jump("own-call") }, "Evaluate your own call")),
      h("ul", { class: "you-get", "aria-label": "What you get" },
        h("li", {}, h("strong", {}, "Verdict"), " Critical Fail, Needs Attention, Meets Bar or Not Evaluable"),
        h("li", {}, h("strong", {}, "Findings"), " each tied to the transcript lines behind it"),
        h("li", {}, h("strong", {}, "Uncertainty"), " what could not be checked, never counted as a pass"),
        h("li", {}, h("strong", {}, "Next step"), " what the reviewer should do"))),
    h("section", { class: "panel", id: "demos", "aria-labelledby": "demos-title" },
      h("div", { class: "section-head" }, h("h2", { id: "demos-title" }, h("span", { class: "step-tag" }, "Step 1"), " Choose a call"),
        sourceChip("DEMO_REPLAY", "DEMO / REPLAY")),
      h("p", { class: "muted small" }, "Start here: five fictional calls, each showing a different outcome. “View evaluation” opens the recorded result in one click. To use your own call, go to step 2."),
      h("div", { class: "demo-grid" }, demoCards),
      h("div", { id: "demo-status", "aria-live": "polite" })),
    h("section", { class: "panel", id: "own-call", "aria-labelledby": "own-title" },
      h("h2", { id: "own-title" }, h("span", { class: "step-tag" }, "Step 2"), " Choose how the call is provided"),
      modeCards,
      h("div", { class: "notice " + (modeInfo.experimental ? "exp" : "info") }, modeInfo.experimental ? h("strong", {}, "EXPERIMENTAL AUDIO. ") : null, modeInfo.note || ""),
      demo && !audioMode ? h("div", { class: "loaded-demo" }, "Loaded demo call: ", h("strong", {}, demo.title), " ", sourceChip("DEMO_REPLAY", "DEMO"),
        " ", h("button", { type: "button", class: "btn btn-link btn-sm", onclick: clearForm }, "Clear")) : null,
      transcriptField, audioField,
      h("div", { class: "field" }, h("label", { for: "call-name", class: "field-label" }, "Call name ", h("span", { class: "muted" }, "(optional)")),
        h("input", { type: "text", id: "call-name", maxlength: "80", value: f.callName, placeholder: "e.g. Evening batch, call 4",
          oninput: (e) => { f.callName = e.target.value; } })),
      methodField),
    h("section", { class: "panel", id: "run-step", "aria-labelledby": "run-title" },
      h("h2", { id: "run-title" }, h("span", { class: "step-tag" }, "Step 3"), " Evaluate"),
      h("div", { class: "submit-row" },
        h("button", { type: "button", class: "btn btn-primary btn-lg", id: "evaluate-btn", onclick: submitForm }, "Evaluate call"),
        h("p", { class: "small muted", id: "expectation" }, expectation)),
      h("div", { id: "eval-status", "aria-live": "polite" })));
}
function switchMode(id) {
  const f = state.form, sample = state.demos.find(d => d.audio);
  state.mode = id; f.live = false;
  if (id === "audio") f.demoId = null;
  if (id === "audio_transcript" && f.audio && f.audio.sample && sample && !f.transcript.trim())
    Object.assign(f, { transcript: sample.transcript, demoId: sample.id, transcriptFile: null });
  renderEvaluate();
}
function jump(id) {
  const el = document.getElementById(id);
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  const target = el.querySelector(id === "demos" ? ".demo-card .btn-primary" : 'input[name="mode"]:checked');
  if (target) target.focus({ preventScroll: true });
}
function tagClass(t) { return /Critical/.test(t) ? "crit" : /attention/i.test(t) ? "warn" : /Not evaluable/i.test(t) ? "ne" : "ok"; }
function focusEnd(id) { const el = document.getElementById(id); if (el) { el.focus(); el.selectionStart = el.selectionEnd = el.value.length; } }
function clearForm() { Object.assign(state.form, { transcript: "", callName: "", demoId: null, transcriptFile: null, audio: null, live: false }); renderEvaluate(); }
function loadDemo(d, scroll) {
  Object.assign(state.form, { transcript: d.transcript, demoId: d.id, transcriptFile: null, callName: "", live: false });
  if (state.mode === "audio") state.mode = "transcript";
  if (!d.audio && state.mode === "audio_transcript") state.form.audio = null;
  renderEvaluate();
  if (scroll) { document.getElementById("own-call").scrollIntoView({ behavior: "smooth", block: "start" }); document.getElementById("evaluate-btn").focus({ preventScroll: true }); }
}
function setAudio(a) { state.form.audio = a; renderEvaluate(); }
async function useSample(d) {
  try {
    const blob = await (await fetch(d.audio.url)).blob();
    state.form.audio = { file: blob, name: d.audio.filename, url: d.audio.url, sample: true };
    state.form.live = false;
    if (state.mode === "audio_transcript") Object.assign(state.form, { transcript: d.transcript, demoId: d.id, transcriptFile: null });
    renderEvaluate();
  } catch (e) { document.getElementById("eval-status").replaceChildren(notice("err", "The sample recording could not be loaded.", [])); }
}

let timer = null;
function startBusy(button, statusEl, text) {
  state.busy = true;
  document.querySelectorAll("button").forEach(b => { if (b.dataset.demo || b.id === "evaluate-btn") b.disabled = true; });
  if (button) { button.dataset.label = button.textContent; button.textContent = "Evaluating…"; }
  setStepper(2, true);
  const t0 = Date.now();
  const line = h("p", { class: "busy-line" }, h("span", { class: "spinner", "aria-hidden": "true" }), text);
  const secs = h("span", { class: "muted" }, "");
  line.append(" ", secs);
  statusEl.replaceChildren(line);
  timer = setInterval(() => { secs.textContent = Math.round((Date.now() - t0) / 1000) + " s"; }, 1000);
}
function stopBusy(button) {
  state.busy = false; clearInterval(timer);
  document.querySelectorAll("button").forEach(b => { if (b.dataset.demo || b.id === "evaluate-btn") b.disabled = false; });
  if (button && button.dataset.label) button.textContent = button.dataset.label;
  setStepper(evaluateStep(), false);
}
async function evaluate(fd, button, statusEl, busyText) {
  if (state.busy) return;
  startBusy(button, statusEl, busyText);
  try {
    const res = await api("/api/evaluate", { method: "POST", body: fd });
    state.lastResult = res; state.origin = "evaluate"; state.selected = 0;
    clearInterval(timer); state.busy = false;
    location.hash = "#/result/" + res.evaluation_id;
  } catch (err) {
    stopBusy(button);
    statusEl.replaceChildren(errorPanel(err));
  }
}
function runDemo(d, button) {
  const fd = new FormData();
  fd.append("mode", "transcript"); fd.append("transcript", d.transcript); fd.append("demo_id", d.id); fd.append("replay", "true");
  evaluate(fd, button, document.getElementById("demo-status"), "Opening the recorded evaluation of “" + d.title + "”…");
}
function submitForm() {
  const f = state.form, fd = new FormData(), cfg = state.config;
  const audioMode = state.mode === "audio";
  if (state.mode !== "transcript" && !f.audio) {
    document.getElementById("eval-status").replaceChildren(notice("err", audioMode ? "Choose a call recording first (or use the sample recording)." : "Audio + Transcript needs the recording as well.", []));
    return;
  }
  fd.append("mode", state.mode);
  if (f.callName.trim()) fd.append("call_name", f.callName.trim());
  if (!audioMode) {
    if (f.transcriptFile) fd.append("transcript_file", f.transcriptFile);
    else fd.append("transcript", f.transcript);
    if (f.demoId) { fd.append("demo_id", f.demoId); fd.append("replay", f.live && cfg.live_available ? "false" : "true"); }
  } else if (f.audio.sample) {
    fd.append("replay", f.live && cfg.live_available ? "false" : "true");
  }
  if (state.mode !== "transcript") fd.append("audio_file", f.audio.file, f.audio.name);
  const replay = audioMode ? f.audio.sample && !(f.live && cfg.live_available) : !!f.demoId && !(f.live && cfg.live_available);
  const live = cfg.live_available && !replay;
  evaluate(fd, document.getElementById("evaluate-btn"), document.getElementById("eval-status"),
    live && audioMode ? "EXPERIMENTAL AUDIO: Gemini is listening to the recording, then the judge checks each rule…"
      : live ? "Evaluating live with Google Gemini (" + cfg.evaluator.model_id + "): reading the call, then checking each rule…" : "Evaluating…");
}
function errorPanel(err) {
  const tips = { AUDIO_FORMAT: "Audio only takes .wav or .mp3; Audio + Transcript also takes .m4a.",
    AUDIO_TOO_LARGE: "Trim the recording, or provide its transcript and use Audio + Transcript.",
    AUDIO_MALFORMED: "Export the recording again as a standard .wav or .mp3 file." }[err.code];
  return notice("err", err.message || "Something went wrong. Nothing was evaluated.", [...(err.pathway || []), ...(tips ? [tips] : [])]);
}

/* ------------------------------------------------------------------ Screen 2: Result */
function renderResult(r) {
  const fromLibrary = state.origin === "library";
  setNav(fromLibrary ? "library" : "evaluate");
  const v = r.verdict && r.verdict.code ? VERDICT[r.verdict.code] : null;
  const findings = r.findings || [];
  const sel = Math.min(state.selected, Math.max(findings.length - 1, 0));
  const selTurns = new Set(findings.length ? findings[sel].evidence.map(e => e.turn).filter(Boolean) : []);

  const context = fromLibrary
    ? h("nav", { class: "breadcrumb", "aria-label": "Breadcrumb" }, h("a", { href: "#/library" }, "Call library"), h("span", { "aria-hidden": "true" }, " › "), h("span", { "aria-current": "page" }, r.call.name))
    : stepper(3);
  const actions = h("div", { class: "row gap-s wrap result-actions" },
    h("a", { class: "btn btn-secondary btn-sm", href: "#/evaluate" }, "← Evaluate another call"),
    fromLibrary ? null : h("a", { class: "btn btn-link btn-sm", href: "#/library" }, "Open the call library"));

  const xa = r.experimental_audio;
  const who = "Provider: " + (r.evaluator.provider_label || r.evaluator.provider) + " · Model: " + r.evaluator.model;
  const srcText = { LIVE: r.evaluator.model_called ? who + " · judged just now." : "Decided just now by the automatic pre-checks; no model call was needed.",
    DEMO_REPLAY: "A fictional demo call. This is its recorded evaluation, replayed through the real rules engine, not a live model result.",
    UNAVAILABLE: "Live evaluation isn't available on this server, so this call was not judged." }[r.source];
  const banner = h("div", { class: "source-banner src-" + r.source }, h("strong", {}, r.source === "UNAVAILABLE" ? "NO VERDICT" : r.source_label), h("span", {}, srcText));
  const xBanner = xa ? h("div", { class: "exp-banner", role: "note" }, h("strong", {}, xa.label),
    h("span", {}, xa.caveat + " Audio reliability: " + xa.audio_reliability + ". " + xa.reliability_scope)) : null;

  // 1 VERDICT + 2 WHY
  const verdictLabel = v ? v.label : r.status === "EVALUATION_FAILED" ? "Evaluation failed" : "No verdict";
  const verdictMeaning = v ? v.meaning : r.status === "EVALUATION_FAILED"
    ? "The evaluation could not be completed, so there is no verdict. A failed evaluation is never a pass."
    : "The call was not judged. Nothing here is a pass.";
  const ev = r.uncertainty ? EVALUABILITY[r.uncertainty.evaluability] : null;
  const hero = h("section", { class: "panel verdict v-" + (v ? v.cls : "none"), "aria-labelledby": "verdict-title" },
    h("p", { class: "small muted" }, [r.call.name, r.call.mode_label + (xa ? " (experimental)" : ""), r.call.n_turns ? r.call.n_turns + " turns" : null,
      r.call.audio_duration_s ? r.call.audio_duration_s + " s of audio" : null].filter(Boolean).join(" · ")),
    r.call.scenario ? h("p", { class: "small scenario" }, "Scenario (fictional): " + r.call.scenario) : null,
    h("h1", { id: "verdict-title", class: "verdict-label" }, verdictLabel,
      r.verdict && r.verdict.critical_status ? h("span", { class: "verdict-sub" }, r.verdict.critical_status === "CONFIRMED" ? " · confirmed" : " · suspected") : null),
    h("p", { class: "verdict-meaning" }, verdictMeaning, v ? h("span", { class: "muted" }, " Holds within the evidence in this call.") : null),
    r.call.audio_url ? h("div", { class: "audio-preview" }, h("span", { class: "small muted" }, r.call.audio_note || "Recording"),
      h("audio", { controls: true, preload: "none", src: r.call.audio_url, "aria-label": "Play the call recording" })) : null,
    h("div", { class: "why" }, h("h2", {}, "Why"),
      findings.length ? [h("p", { class: "why-title" }, findings[0].name || findings[0].code, h("span", { class: "muted why-code" }, " " + (findings[0].is_gate ? "hard rule " : "") + findings[0].code)),
        (findings[0].evidence || []).filter(e => e.turn).slice(0, 1).map(e => h("p", { class: "why-quote" }, "Turn " + e.turn + " · " + (e.role === "BORROWER" ? "Borrower" : "Agent") + ": “" + e.quote + "”")),
        findings.length > 1 ? h("p", { class: "small muted" }, "+ " + (findings.length - 1) + " more finding" + (findings.length > 2 ? "s" : "") + " below.") : null]
        : [h("p", { class: "why-title" }, r.key_finding.title), r.key_finding.explanation ? h("p", { class: "small" }, r.key_finding.explanation) : null]),
    h("ul", { class: "facts", "aria-label": "Summary" },
      ev ? h("li", { class: "fact f-" + ev.cls }, h("span", { class: "fact-k" }, "Evaluability"), h("span", { class: "fact-v" }, ev.label)) : null,
      r.tags ? h("li", { class: "fact " + (r.tags.dangerous_win !== "NONE" ? "f-crit" : "") }, h("span", { class: "fact-k" }, "Dangerous Win"), h("span", { class: "fact-v" }, r.tags.dangerous_win === "NONE" ? "No" : "Yes (" + r.tags.dangerous_win.toLowerCase() + ")")) : null,
      r.tags ? h("li", { class: "fact " + (r.tags.clean_loss ? "f-ok" : "") }, h("span", { class: "fact-k" }, "Clean Loss"), h("span", { class: "fact-v" }, r.tags.clean_loss ? "Yes" : "No")) : null,
      r.status === "OK" ? h("li", { class: "fact" }, h("span", { class: "fact-k" }, "Findings"), h("span", { class: "fact-v" }, String(findings.length))) : null,
      xa ? h("li", { class: "fact f-warn" }, h("span", { class: "fact-k" }, "Audio reliability"), h("span", { class: "fact-v" }, xa.audio_reliability)) : null),
    r.status !== "OK" ? h("div", { class: "row gap-s wrap mt-s" },
      h("a", { class: "btn btn-primary btn-sm", href: "#/evaluate" }, "Try a demo call")) : null);

  // 3 FINDINGS
  const findingsSec = r.status === "OK" ? h("section", { class: "panel", "aria-labelledby": "findings-title" },
    h("h2", { id: "findings-title" }, "Findings (" + findings.length + ")"),
    findings.length ? h("p", { class: "small muted" }, "Select a finding to highlight its evidence in the transcript below.")
      : h("p", {}, "No hard rule was broken and no defect was found in what could be checked."),
    h("div", { class: "finding-list", role: "list" }, findings.map((f, i) => findingCard(f, i, i === sel))))
    : h("section", { class: "panel", "aria-labelledby": "ran-title" }, h("h2", { id: "ran-title" }, "What did run"),
      h("p", { class: "small" }, r.status === "EVALUATION_FAILED" ? "The model evaluation started but did not complete, so no findings are reported."
        : xa && !r.frontend ? "Nothing ran: the recording was not listened to, so there is no transcript and no rule was judged." : "Only the automatic pre-checks ran; no rule was judged."),
      r.frontend ? h("p", { class: "small muted" }, "Pre-checks: the call looks " + (r.frontend.evaluability === "EVALUABLE" ? "evaluable" : r.frontend.evaluability.toLowerCase().replace("_", " ")) + (r.frontend.reason_text ? " (" + r.frontend.reason_text + ")" : "") + ".") : null);

  // 4 EVIDENCE
  const cited = new Set((r.transcript || []).filter(t => t.cited).map(t => t.turn));
  const commitTurns = new Set((r.unverified_commitments || []).map(e => e.turn));
  const anyUnreliable = (r.transcript || []).some(t => t.unreliable);
  const evidence = (r.transcript || []).length ? h("section", { class: "panel", "aria-labelledby": "evidence-title" },
    h("h2", { id: "evidence-title" }, xa ? "Evidence: the transcript Gemini heard (experimental)" : "Evidence: the transcript"),
    xa ? h("p", { class: "small muted" }, xa.source === "DEMO_REPLAY" ? "Replay: these turns come from the sample recording's own script." : "Transcribed and speaker-labelled by Gemini from the recording. Not independently checked: listen to the recording before acting on a finding.") : null,
    findings.length || anyUnreliable ? h("p", { class: "legend small" }, findings.length ? [h("span", { class: "sw sw-strong" }), " selected finding  ", h("span", { class: "sw sw-soft" }), " other cited lines  "] : null,
      commitTurns.size ? [h("span", { class: "sw sw-commit" }), " agent promise (unverified)  "] : null,
      anyUnreliable ? [h("span", { class: "sw sw-unrel" }), " unreliable: unknown speaker or unclear speech"] : null) : null,
    h("div", { class: "transcript" }, (r.transcript || []).map(t => h("div", {
      id: "turn-" + t.turn,
      class: "turn" + (selTurns.has(t.turn) ? " hl-strong" : cited.has(t.turn) ? " hl-soft" : "") + (commitTurns.has(t.turn) ? " hl-commit" : "") + (t.unreliable ? " unreliable" : "") },
      h("span", { class: "n" }, t.turn), h("span", { class: "r" }, t.role === "BORROWER" ? "Borrower" : t.role === "AGENT" ? "Agent" : t.role === "UNKNOWN" ? "Speaker unknown" : t.role === "OTHER" ? "Other" : t.role),
      h("span", { class: "t" }, t.text, t.unreliable ? h("span", { class: "sr-only" }, " (unreliable)") : null)))),
    (r.unverified_commitments || []).length ? h("div", { class: "mt" },
      h("h3", {}, "Agent promises to verify"),
      h("p", { class: "small muted" }, "Things the agent said it would do. Whether they were done needs systems outside the call, so they are listed for follow-up, not judged."),
      r.unverified_commitments.map(quote)) : null) : null;

  // Audio reliability (experimental audio only)
  const audioRel = xa ? h("section", { class: "panel audio-rel", "aria-labelledby": "arel-title" },
    h("div", { class: "section-head" }, h("h2", { id: "arel-title" }, "Audio reliability"), chip(xa.audio_reliability, "exp")),
    h("p", { class: "small" }, xa.caveat),
    h("dl", { class: "kv" },
      h("dt", {}, "How the recording was read"), h("dd", {}, xa.interpreter),
      h("dt", {}, "Speaker attribution"), h("dd", {}, xa.speaker_attribution ? [h("strong", {}, { CLEAR: "Agent and borrower separated", PARTIAL: "Partly separated", UNCERTAIN: "Could not be separated", NO_SPEECH: "No conversation heard" }[xa.speaker_attribution.status] || xa.speaker_attribution.status), ". ", xa.speaker_attribution.text,
        xa.speaker_attribution.note ? h("span", { class: "muted" }, " Gemini's own note: “" + xa.speaker_attribution.note + "”") : null] : "Not run."),
      h("dt", {}, "Transcription"), h("dd", {}, xa.transcription ? xa.transcription.text + (xa.transcription.language ? " Language: " + xa.transcription.language + "." : "") : "Not run."),
      h("dt", {}, "Timing"), h("dd", {}, xa.timing),
      h("dt", {}, "Reliability claims"), h("dd", {}, xa.reliability_scope)),
    h("p", { class: "small muted" }, "No confidence score is shown for the transcription itself: none has been measured.")) : null;

  // 5 CONFIDENCE / UNCERTAINTY
  const u = r.uncertainty;
  const external = u ? u.out_of_scope.filter(c => c.reason_code === "EXTERNAL_DATA_REQUIRED") : [];
  const modeCap = u ? u.out_of_scope.filter(c => c.reason_code !== "EXTERNAL_DATA_REQUIRED") : [];
  const uncertainty = u ? h("section", { class: "panel", "aria-labelledby": "unc-title" },
    h("h2", { id: "unc-title" }, "Confidence and what could not be checked"),
    h("div", { class: "eval-box f-" + ev.cls }, h("strong", {}, ev.label), h("span", {}, ev.text), u.reason_text ? h("span", { class: "small" }, "Reason: " + u.reason_text + ".") : null),
    h("div", { class: "unc-grid" },
      h("div", {}, h("h3", {}, "Could not be decided (" + u.inconclusive.length + ")"),
        h("p", { class: "small muted" }, "Relevant here, but not decidable from this call: a threshold awaiting sign-off, unclear speech or missing context. Never counted as a pass."),
        u.inconclusive.length ? collapsible(u.inconclusive.length > 6, "Show all " + u.inconclusive.length + " checks",
          h("ul", { class: "plain-list" }, u.inconclusive.map(c => h("li", {}, h("strong", {}, c.name || c.code), h("span", { class: "muted" }, " (" + c.code + ")"), ": " + c.reasons))))
          : h("p", { class: "small" }, "None.")),
      h("div", {}, h("h3", {}, "Not verifiable from the call alone"),
        external.length ? h("p", { class: "small" }, "Payment status, account figures and follow-up actions could not be externally verified: they need payment, account or CRM records.") : h("p", { class: "small" }, "None."),
        external.length ? h("details", {}, h("summary", {}, "Show the " + external.length + " checks"), h("ul", { class: "plain-list small" }, external.map(c => h("li", {}, (c.name || c.code) + " (" + c.code + ")")))) : null,
        modeCap.length ? h("details", {}, h("summary", {}, "Not observable in this input (" + modeCap.length + ")"), h("ul", { class: "plain-list small" }, modeCap.map(c => h("li", {}, (c.name || c.code) + " (" + c.code + "): " + c.reason))))
          : null)),
    h("p", { class: "small muted" }, "Confidence is " + (u.confidence_source === "SELF_REPORTED" ? "self-reported by the model." : "computed by the rules engine from the evidence, not self-reported by the model."))) : null;

  // Outcome tags (diagnostic)
  const outcome = r.tags ? h("section", { class: "panel", "aria-labelledby": "outcome-title" },
    h("h2", { id: "outcome-title" }, "Outcome: did the result come the right way?"),
    h("div", { class: "tag-grid" },
      h("div", { class: "tag-card " + (r.tags.dangerous_win !== "NONE" ? "on-crit" : "") },
        h("h3", {}, "Dangerous Win: " + (r.tags.dangerous_win === "NONE" ? "No" : "Yes (" + r.tags.dangerous_win.toLowerCase() + ")")),
        h("p", { class: "small" }, DW_HELP), r.tags.dangerous_win !== "NONE" ? h("p", { class: "small strong" }, r.tags.dangerous_win_text) : null),
      h("div", { class: "tag-card " + (r.tags.clean_loss ? "on-ok" : "") },
        h("h3", {}, "Clean Loss: " + (r.tags.clean_loss ? "Yes" : "No")),
        h("p", { class: "small" }, CL_HELP))),
    r.outcome ? h("p", { class: "small muted" }, "Call outcome: " + (r.outcome.dispositions || []).map(x => x.toLowerCase().replace(/_/g, " ")).join(", ") +
      (r.outcome.positive ? " (positive)" : " (no positive outcome)") + (r.outcome.firmness ? ", commitment " + r.outcome.firmness : "") +
      (r.outcome.outcome_attribution ? ", driven by " + ({ AGENT_DRIVEN: "the agent", CUSTOMER_DRIVEN: "the customer", POLICY_DRIVEN: "policy", INDETERMINATE: "unclear factors" }[r.outcome.outcome_attribution] || r.outcome.outcome_attribution.toLowerCase()) : "") + ".") : null) : null;

  // 6 ATTRIBUTION
  const attribution = findings.length ? h("section", { class: "panel", "aria-labelledby": "attr-title" },
    h("h2", { id: "attr-title" }, "Attribution"),
    h("p", { class: "small muted" }, "Who or what each finding is attributed to, by rubric rule. The model's reasoning is not shown."),
    h("ul", { class: "plain-list" }, findings.map(f => h("li", {}, h("strong", {}, f.name || f.code), " → " + (f.attribution ? f.attribution.label : "not attributed"),
      f.attribution && f.attribution.secondary ? " (also " + f.attribution.secondary.toLowerCase().replace(/_/g, " ") + ")" : "")))) : null;

  // 7 ACTION
  const action = h("section", { class: "panel next-step", "aria-labelledby": "next-title" },
    h("h2", { id: "next-title" }, "Next step"),
    h("p", { class: "strong" }, r.action),
    r.routing ? h("p", { class: "small muted" }, "Review queue: " + r.routing.label) : null,
    h("div", { class: "row gap-s wrap" },
      h("a", { class: "btn btn-primary btn-sm", href: "#/evaluate" }, "Evaluate another call"),
      h("a", { class: "btn btn-secondary btn-sm", href: "#/library" }, "Open the call library"),
      h("a", { class: "btn btn-secondary btn-sm", href: "#/reliability" }, "Explore evaluator reliability"),
      r.record ? h("button", { type: "button", class: "btn btn-link btn-sm", onclick: () => downloadRecord(r) }, "Download the evaluation record (JSON)") : null));

  page(h("div", { class: "result-top" }, context, actions), banner, xBanner, hero,
    h("div", { class: "grid" },
      h("div", { class: "col-main" }, findingsSec, evidence, uncertainty, audioRel, outcome, attribution, action),
      h("aside", { class: "col-side", "aria-label": "About this result" }, readingGuide(), evaluatorPanel(r), profilePanel())));
}
function collapsible(collapse, label, content) {
  return collapse ? h("details", {}, h("summary", {}, label), content) : content;
}
function findingCard(f, i, selected) {
  const select = () => { state.selected = i; renderResult(state.lastResult); const t = (f.evidence || []).find(e => e.turn); if (t) flashTurn(t.turn, false);
    const b = document.querySelectorAll(".finding-select")[i]; if (b) b.focus({ preventScroll: true }); };
  return h("div", { role: "listitem", class: "finding" + (selected ? " selected" : "") },
    h("button", { type: "button", class: "finding-select", "aria-pressed": selected ? "true" : "false", onclick: select },
      h("span", { class: "finding-head" }, h("span", { class: "finding-name" }, f.name || f.code),
        chip(SEVERITY[f.severity] || f.severity, "sev-" + f.severity),
        chip(f.is_gate ? (f.status.includes("CONFIRMED") ? "Hard rule · confirmed" : "Hard rule · suspected") : (f.status === "ASSERTED" ? "Found" : "Possible"), "plain"),
        h("span", { class: "code" }, f.code)),
      h("span", { class: "finding-meta small" }, [f.confidence ? f.confidence.toLowerCase() + " confidence" : null, f.attribution ? "attributed to " + f.attribution.label.toLowerCase() : null, f.dimension].filter(Boolean).join(" · ")),
      selected ? null : h("span", { class: "small muted" }, "Show evidence")),
    selected ? h("div", { class: "finding-body" },
      (f.evidence || []).map(quote),
      f.evidence_unverified ? h("p", { class: "small warn-text" }, "The cited evidence could not be verified against the transcript.") : null,
      f.explanation ? h("details", {}, h("summary", { class: "small" }, "Rule applied"), h("p", { class: "small muted" }, f.explanation)) : null) : null);
}
function quote(e) {
  if (!e.turn) return h("div", { class: "quote" }, "Transcript header: " + (e.header_field || ""));
  return h("div", { class: "quote" }, h("button", { type: "button", class: "btn btn-link btn-xs", onclick: () => flashTurn(e.turn, true) }, "Turn " + e.turn),
    " ", (e.role === "BORROWER" ? "Borrower" : "Agent") + ": “" + (e.quote || "") + "”");
}
function flashTurn(n, scroll) {
  const el = document.getElementById("turn-" + n);
  if (!el) return;
  el.scrollIntoView({ block: "center", behavior: scroll ? "smooth" : "auto" });
  el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1400);
}
function downloadRecord(r) {
  const body = r.experimental_audio ? { experimental_audio: { label: r.experimental_audio.label, caveat: r.experimental_audio.caveat, audio_reliability: r.experimental_audio.audio_reliability, interpreter: r.experimental_audio.interpreter }, record: r.record } : r.record;
  const blob = new Blob([JSON.stringify(body, null, 2)], { type: "application/json" });
  const a = h("a", { href: URL.createObjectURL(blob), download: r.evaluation_id + ".json" });
  document.body.append(a); a.click(); a.remove();
}
function readingGuide() {
  return h("section", { class: "panel side", "aria-labelledby": "guide-title" }, h("h2", { id: "guide-title", class: "h3" }, "How to read a verdict"),
    h("dl", { class: "scale" }, Object.values(VERDICT).map(x => [h("dt", {}, h("span", { class: "dot d-" + x.cls, "aria-hidden": "true" }), x.label), h("dd", {}, x.meaning)])));
}
function evaluatorPanel(r) {
  const e = r.evaluator, u = r.usage || {};
  return h("details", { class: "panel side" }, h("summary", {}, "Evaluator details"),
    h("dl", { class: "kv" },
      h("dt", {}, "Source"), h("dd", {}, sourceChip(r.source, r.source_label)),
      h("dt", {}, "Evaluator"), h("dd", {}, e.system + " " + e.system_version),
      h("dt", {}, "Provider"), h("dd", {}, e.provider_label || e.provider),
      r.experimental_audio ? [h("dt", {}, "Input"), h("dd", {}, "Audio: EXPERIMENTAL (" + (e.audio_prompt_version || "") + ")")] : null,
      h("dt", {}, "Model"), h("dd", { class: "mono" }, e.model),
      (e.served_model_versions || []).length ? [h("dt", {}, "Served version"), h("dd", { class: "mono" }, e.served_model_versions.join(", "))] : null,
      h("dt", {}, "Prompt"), h("dd", { class: "mono" }, e.prompt_version),
      h("dt", {}, "Rules engine"), h("dd", { class: "mono" }, e.engine_version),
      h("dt", {}, "Model calls"), h("dd", {}, String(e.model_called ? u.llm_calls || 0 : 0)),
      h("dt", {}, "Tokens in / out"), h("dd", {}, (u.input_tokens || 0) + " / " + (u.output_tokens || 0)),
      h("dt", {}, "Time"), h("dd", {}, (u.latency_s || 0) + " s"),
      h("dt", {}, "Evaluated at"), h("dd", { class: "small" }, new Date(r.created_at).toLocaleString())));
}
function profilePanel() {
  const p = state.config && state.config.profile;
  if (!p) return null;
  return h("details", { class: "panel side" }, h("summary", {}, "Evaluation profile (rules used, read-only)"),
    h("p", { class: "small muted" }, p.disclaimer),
    h("dl", { class: "kv" },
      h("dt", {}, "Profile"), h("dd", { class: "mono" }, p.profile_id + " " + p.profile_version),
      h("dt", {}, "Rubric"), h("dd", { class: "mono" }, p.rubric_version),
      p.settings.map(s => [h("dt", {}, s.name), h("dd", {}, s.value.replace(/_/g, " "))])),
    h("h3", { class: "h4" }, "Hard rules (" + p.gates.length + ")"),
    h("ul", { class: "plain-list small" }, p.gates.map(g => h("li", {}, g.name + " (" + g.id + ")"))),
    h("p", { class: "small muted" }, p.pending_signoff.count + " profile values await human sign-off; they are never filled in by default."));
}

/* ------------------------------------------------------------------ Screen 3: Call library */
async function renderLibrary(cached) {
  setNav("library");
  const L = state.library;
  let rows = cached ? L.rows : null;
  if (!rows) {
    page(h("p", { class: "busy-line" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Loading the call library…"));
    try { rows = L.rows = await api("/api/calls"); } catch (err) { page(errorPanel(err)); return; }
  }
  const verdictOf = r => r.verdict_code || (r.status === "EVALUATION_FAILED" ? "FAILED" : "NONE");
  const filters = [["all", "All"], ["CRITICAL_FAIL", "Critical Fail"], ["NEEDS_ATTENTION", "Needs Attention"], ["MEETS_BAR", "Meets Bar"], ["NOT_EVALUABLE", "Not Evaluable"], ["NONE", "No verdict"]];
  const count = k => rows.filter(r => k === "all" || verdictOf(r) === k || (k === "NONE" && verdictOf(r) === "FAILED")).length;
  const shown = rows.filter(r => (L.verdict === "all" || verdictOf(r) === L.verdict || (L.verdict === "NONE" && verdictOf(r) === "FAILED"))
    && (L.source === "all" || (L.source === "live" ? r.source === "LIVE" : r.source === "DEMO_REPLAY"))
    && (!L.q || (r.name + " " + (r.primary_finding || "")).toLowerCase().includes(L.q.toLowerCase())));
  const open = r => { state.origin = "library"; state.selected = 0; location.hash = "#/result/" + r.evaluation_id; };
  const list = shown.length ? h("div", { class: "table-wrap" }, h("table", { class: "calls" },
    h("caption", { class: "sr-only" }, "Evaluated calls. Select a row to open its result."),
    h("thead", {}, h("tr", {}, ["Call", "Input", "Verdict", "Key finding", "Source", ""].map(c => h("th", { scope: "col" }, c)))),
    h("tbody", {}, shown.map(r => h("tr", { class: "row-link", tabindex: "0", "aria-label": "Open the result for " + r.name,
      onclick: () => open(r), onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(r); } } },
      h("td", { "data-label": "Call" }, h("strong", {}, r.name), h("div", { class: "small muted" }, new Date(r.created_at).toLocaleTimeString())),
      h("td", { "data-label": "Input" }, r.modality, r.experimental_audio ? [" ", chip("EXPERIMENTAL", "exp")] : null),
      h("td", { "data-label": "Verdict" }, verdictPill(r)),
      h("td", { "data-label": "Key finding" }, r.primary_finding || "-"),
      h("td", { "data-label": "Source" }, sourceChip(r.source, r.source === "UNAVAILABLE" ? "NO VERDICT" : r.source_label)),
      h("td", { class: "open-cell", "aria-hidden": "true" }, "Open ›"))))))
    : h("div", { class: "empty" }, h("p", {}, "No calls match these filters."),
      h("button", { type: "button", class: "btn btn-secondary btn-sm", onclick: () => { Object.assign(L, { verdict: "all", source: "all", q: "" }); renderLibrary(true); } }, "Clear filters"));
  const search = h("input", { type: "search", id: "lib-search", value: L.q, placeholder: "Search by call name or finding",
    oninput: (e) => { L.q = e.target.value; renderLibrary(true).then(() => focusEnd("lib-search")); } });
  page(
    h("header", { class: "page-head" }, h("h1", {}, "Call library"),
      h("p", { class: "lead" }, "Every call evaluated on this server since it started, plus the five demo calls. Open a call to review its verdict and evidence."),
      h("p", { class: "small muted" }, "Kept in memory while the server runs; fictional demo and development calls only.")),
    h("section", { class: "panel" },
      h("div", { class: "filters" },
        h("div", { class: "filter-group", role: "group", "aria-label": "Filter by verdict" }, filters.map(([k, label]) =>
          h("button", { type: "button", class: "filter" + (L.verdict === k ? " on" : ""), "aria-pressed": L.verdict === k ? "true" : "false",
            onclick: () => { L.verdict = k; renderLibrary(true); } }, label + " (" + count(k) + ")"))),
        h("div", { class: "filter-group", role: "group", "aria-label": "Filter by source" }, [["all", "All sources"], ["live", "Live"], ["demo", "Demo"]].map(([k, label]) =>
          h("button", { type: "button", class: "filter" + (L.source === k ? " on" : ""), "aria-pressed": L.source === k ? "true" : "false",
            onclick: () => { L.source = k; renderLibrary(true); } }, label))),
        h("label", { class: "search" }, h("span", { class: "sr-only" }, "Search calls"), search)),
      list,
      h("div", { class: "row gap-s mt" }, h("a", { class: "btn btn-primary btn-sm", href: "#/evaluate" }, "Evaluate a new call"))));
}
function verdictPill(r) {
  const v = VERDICT[r.verdict_code];
  if (v) return h("span", { class: "pill p-" + v.cls }, v.label);
  return h("span", { class: "pill p-ne" }, r.status === "EVALUATION_FAILED" ? "Evaluation failed" : "No verdict");
}

/* ------------------------------------------------------------------ Screen 4: Evaluator reliability */
async function renderReliability() {
  setNav("reliability");
  page(h("p", { class: "busy-line" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Loading reliability measurements…"));
  let r;
  try { r = await api("/api/reliability"); } catch (err) { page(errorPanel(err)); return; }
  const head = h("header", { class: "page-head" }, h("h1", {}, "Evaluator reliability"),
    h("p", { class: "lead" }, "How closely the evaluator's judgements match the intended answers: what has been measured during development, and what final validation still requires."));
  if (r.status !== "OK") { page(head, notice("warn", r.message, [])); return; }
  const names = Object.keys(r.systems);
  const executed = names.filter(n => r.systems[n].status === "EXECUTED");
  const notRun = names.filter(n => r.systems[n].status !== "EXECUTED");
  const measuredLines = executed.map(n => {
    const m = r.systems[n].metrics || {};
    return h("li", {}, h("strong", {}, SYSTEM_NAMES[n] + " (" + n + "): "), "verdict matched on " + pct(m.verdict_accuracy) + " of calls; caught " + pct(m.critical_recall) + " of intended hard-rule failures.");
  });
  const summary = h("div", { class: "rel-grid" },
    h("section", { class: "panel rel-card", "aria-labelledby": "dev-title" },
      h("div", { class: "section-head" }, h("h2", { id: "dev-title" }, "Development measurement"), chip("DEV ENGINEERING MEASUREMENT", "warn")),
      h("h3", {}, "What was tested"),
      h("p", { class: "small" }, r.benchmark.items_executed + " fictional development calls (draft transcripts awaiting human review), evaluated from transcripts. Each result is compared with the intended outcome in the call's design, not with final gold labels."),
      h("h3", {}, "Measured so far"),
      h("ul", { class: "plain-list small" }, measuredLines,
        notRun.length ? h("li", {}, h("strong", {}, notRun.map(n => SYSTEM_NAMES[n] ? n : n).join(", ") + ": "), "not measured yet. The development run with the live model has not been executed; the model-based numbers stay empty until it is (see Technical details).") : null,
        r.reproducibility ? h("li", {}, h("strong", {}, "Repeatability: "), r.reproducibility.repetitions + " identical runs, results " + (r.reproducibility.stable ? "stable." : "not stable.")) : null)),
    h("section", { class: "panel rel-card pending-card", "aria-labelledby": "final-title" },
      h("div", { class: "section-head" }, h("h2", { id: "final-title" }, "Final reliability validation"), chip("PENDING", "crit")),
      h("p", { class: "small" }, "Final validation proves the evaluator works on calls it has never seen. It needs:"),
      h("ul", { class: "check-list small" },
        h("li", {}, "Transcripts reviewed by a native Hindi / Hinglish speaker"),
        h("li", {}, "Final gold labels (the agreed correct answers)"),
        h("li", {}, "A holdout run on unseen calls: " + r.holdout),
        h("li", {}, "A red-team run on adversarial calls: " + r.red_team)),
      h("p", { class: "small strong" }, "Until these are complete, no number on this page is evidence that the evaluator works.")));
  const xp = r.experimental_audio_path;
  const audioPath = xp ? h("section", { class: "panel exp-card", "aria-labelledby": "xa-title" },
    h("div", { class: "section-head" }, h("h2", { id: "xa-title" }, "Experimental audio path"), chip("EXPERIMENTAL · NOT IN RELIABILITY CLAIMS", "exp")),
    h("p", { class: "strong" }, xp.text),
    h("p", { class: "small" }, xp.how),
    h("p", { class: "small muted" }, "Pending before audio can be measured: " + xp.pending)) : null;
  const table = h("section", { class: "panel", "aria-labelledby": "cmp-title" },
    h("h2", { id: "cmp-title" }, "Evaluators compared on the development calls"),
    h("p", { class: "small muted" }, "The app uses B. The others are comparison points. UNMEASURED means the run gives the number no support; it is never filled in."),
    h("div", { class: "table-wrap" }, h("table", { class: "metrics" },
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Measure"), names.map(n => h("th", { scope: "col" }, n, h("div", { class: "small muted th-sub" }, SYSTEM_NAMES[n] || ""))))),
      h("tbody", {},
        h("tr", {}, h("th", { scope: "row" }, "Status"), names.map(n => h("td", {}, r.systems[n].status === "EXECUTED" ? chip("Measured", "ok") : chip("Not run yet", "ne")))),
        METRICS.map(([k, label, help]) => h("tr", {}, h("th", { scope: "row" }, label, h("div", { class: "small muted" }, help)),
          names.map(n => h("td", {}, r.systems[n].status === "EXECUTED" ? pct((r.systems[n].metrics || {})[k]) : "–")))),
        h("tr", {}, h("th", { scope: "row" }, "Failures and abstentions", h("div", { class: "small muted" }, "Evaluation failures · not evaluable · partially evaluable")),
          names.map(n => { const a = r.systems[n].abstention; return h("td", {}, a ? a.evaluation_failed + " · " + a.not_evaluable + " · " + a.partial : "–"); }))))));
  const unmeasured = h("section", { class: "panel", "aria-labelledby": "unm-title" }, h("h2", { id: "unm-title" }, "Not measurable in this run"),
    r.unmeasured.length ? h("ul", { class: "plain-list small" }, r.unmeasured.map(x => h("li", {}, x))) : h("p", { class: "small" }, "None."));
  const tech = h("details", { class: "panel" }, h("summary", {}, "Technical details"),
    h("dl", { class: "kv" },
      h("dt", {}, "Label"), h("dd", {}, r.label),
      h("dt", {}, "Evaluator in the app"), h("dd", {}, r.evaluator.system + " · " + r.evaluator.system_version),
      h("dt", {}, "Provider / model"), h("dd", { class: "mono" }, r.evaluator.provider + " · " + r.evaluator.model),
      h("dt", {}, "Prompt / engine"), h("dd", { class: "mono" }, r.evaluator.prompt_version + " · " + r.evaluator.engine_version),
      h("dt", {}, "Live on this server"), h("dd", {}, r.evaluator.live_available ? "yes" : "no"),
      h("dt", {}, "Run"), h("dd", { class: "mono" }, r.run.run_id + " · commit " + r.run.git_commit + " · " + r.run.repetitions + " repetition(s)"),
      h("dt", {}, "Input"), h("dd", {}, r.run.unit_mode + " · " + r.run.asr_mode),
      h("dt", {}, "Reference"), h("dd", {}, r.benchmark.reference),
      h("dt", {}, "Excluded"), h("dd", {}, r.benchmark.items_excluded + " items (component snippets and a tuning-only copy)"),
      h("dt", {}, "Pre-checks"), h("dd", {}, "evaluability agreement " + pct(r.frontend.evaluability_agreement) + " · calling-hours agreement " + pct(r.frontend.g7_agreement)),
      h("dt", {}, "Gold"), h("dd", {}, r.gold),
      notRun.length ? [h("dt", {}, "Not executed"), h("dd", {}, notRun.join(", ") + ": " + (r.systems[notRun[0]].reason || ""))] : null));
  page(head, h("h2", { class: "rel-section" }, "Evaluator reliability: current development evidence"), summary, audioPath, table, unmeasured, tech);
}

/* ------------------------------------------------------------------ routing */
async function route() {
  const hash = location.hash || "#/evaluate";
  const parts = hash.slice(2).split("/");
  window.scrollTo(0, 0);
  if (parts[0] === "result" && parts[1]) {
    if (!(state.lastResult && state.lastResult.evaluation_id === parts[1])) {
      try { state.lastResult = await api("/api/calls/" + encodeURIComponent(parts[1])); state.selected = 0; }
      catch (err) { setNav("library"); page(errorPanel(err), h("a", { class: "btn btn-secondary btn-sm", href: "#/library" }, "Open the call library")); return; }
      if (state.origin !== "evaluate") state.origin = "library";
    }
    renderResult(state.lastResult);
  } else if (parts[0] === "library") await renderLibrary();
  else if (parts[0] === "reliability") await renderReliability();
  else renderEvaluate();
  if (!state.busy) view.focus({ preventScroll: true });
}

async function boot() {
  document.getElementById("help-toggle").addEventListener("click", () => toggleHelp());
  try {
    const [cfg, demos] = await Promise.all([api("/api/config"), api("/api/demo-calls")]);
    state.config = cfg; state.demos = demos;
    const ls = document.getElementById("live-status");
    ls.textContent = cfg.live_available ? "Live evaluation on · Google Gemini · " + cfg.evaluator.model_id : "Demo mode · live evaluation off";
    ls.title = cfg.live_available ? "Calls you submit are judged live by the model." : "Demo calls show recorded evaluations; your own calls get the automatic pre-checks only.";
    ls.classList.toggle("on", !!cfg.live_available);
    document.getElementById("footer-version").textContent = "v" + cfg.product.version + " · evaluator " + cfg.evaluator.system + " " + cfg.evaluator.system_version;
  } catch (err) { page(errorPanel(err)); return; }
  if (!store("ignosis.help.dismissed")) toggleHelp(true);
  window.addEventListener("hashchange", () => { if (!location.hash.startsWith("#/result/")) state.origin = location.hash.startsWith("#/library") ? "library" : "evaluate"; route(); });
  route();
}
boot();

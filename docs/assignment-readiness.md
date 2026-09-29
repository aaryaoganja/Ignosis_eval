# Assignment readiness: Ignosis Voice AI Quality Evaluator

Audit of the repository and the review-app prototype against the assignment, at commit `256b590` plus this audit's
copy fixes. Every status says what exists, and distinguishes *designed* from *delivered*.

## Where the requirements come from (read first)

The verbatim assignment brief is **not in this repository**. It could not be read from the shared Claude Chat
conversation either: that page renders client-side, and its data endpoint refuses unauthenticated access. So this
audit does not quote wording it cannot see. The requirements below come from three sources, each labelled:

- **[Owner summary]**: the owner's own summary of the assignment, verbatim: "a product that evaluates quality of
  Voice AI calls, including: 1. Define what makes a good/bad call without an existing rubric. 2. Build the evaluator.
  3. Demonstrate/prove evaluator reliability."
- **[Contract §1]**: `docs/spec/frozen-contract.md` §1 *Product scope*, including its list "Explicitly not in the
  assignment". These are the Stage 1–4 decisions derived from the brief.
- **[Scope]**: the product scope set at the start of implementation. MVP inputs are exactly audio only, transcript
  only, and audio + transcript. The primary use case is collections. There are no CRM / LMS / account / payment
  integrations, no Policy Pack upload, no agent / tool traces, no production APIs and no real-time intervention.

> **To complete before submission:** paste the verbatim brief here, and check that every requirement in it maps to a
> row below. If the brief asks for a deliverable not listed here (for example a written memo or slide deck), that
> deliverable lives outside this repository (for example in the Claude Chat Stage 1–4 documents).

## Status vocabulary

- **IMPLEMENTED**: exists in code and works (tests, local runs).
- **DESIGNED**: specified in the frozen spec pack, not yet executed.
- **MEASURED**: actually run on the benchmark with a real provider. A local fake Gemini server is not a real run.
- **PENDING**: needs human input, a provider key or benchmark content.
- Row status: **COMPLETE** (the requirement is met for a credible prototype), **PARTIAL** (met in design or
  implementation but not yet measured or executed), **PENDING** (not started, and blocked on external input).

---

## A. Problem framing

| # | Requirement | Evidence | Status | What remains |
|---|---|---|---|---|
| A1 | Clear problem definition [Owner summary 1; Contract §1] | Contract §1: "A call-quality evaluator for AI-agent **collections** calls in BFSI … conceived as a module of Ignosis's Voice AI / Speech Analytics workflow". For each call it produces a verdict, non-compensatory gate results, evidence-backed findings, attribution, outcome with Dangerous Win / Clean Loss, evaluability and an explicit out-of-scope list. | COMPLETE | None |
| A2 | Clear user | AI Quality Reviewer / Operations QA Reviewer (README "Review app"; app footer; `/api/config` `primary_user`). | COMPLETE | None |
| A3 | Clear job to be done | Contract §1 "Primary decision served (v1): Route the calls that need human action (Review / Remediate / Fix), each with verifiable evidence." Result screen: verdict → evidence → **Next step** with a routing tier (1–6). | COMPLETE | None |
| A4 | Ignosis relevance | Contract §1 (a module of Ignosis's Voice AI / Speech Analytics workflow); the app tagline "Ignosis already listens to every call. This layer judges whether the AI agent behaved correctly." | COMPLETE | None |
| A5 | "Quality" operationalized, not reduced to sentiment or outcome | Hard gates are non-compensatory; behavioural dimensions have coded, evidence-backed findings; outcome is tracked separately (contract §10); tone is explicitly "Not evaluated" (contract §8). | COMPLETE | The Stage 1–3 narrative (research, alternatives considered) lives in the Claude Chat documents, not in this repository. Attach them if the brief asks for the reasoning. |

## B. Definition of quality ("what makes a good/bad call without an existing rubric")

| # | Requirement | Evidence | Status | What remains |
|---|---|---|---|---|
| B1 | Good vs bad explicitly defined [Owner summary 1] | Rubric `1.2-mvp` (`docs/spec/rubric.yaml`) and contract §4–§5, §10. Verdicts: **Critical Fail / Needs Attention / Meets Bar / Not Evaluable**, "within available evidence". There are 27 scored codes, 4 platform signals (not agent conduct) and 13 out-of-scope codes. | COMPLETE (DESIGNED + IMPLEMENTED in `engine/`) | The profile values are `FROZEN_FOR_ASSIGNMENT` (a demonstration profile, not Ignosis policy); B-11 sign-off before a benchmark freeze. Not a submission blocker. |
| B2 | Critical gates clear | 9 hard gates: G1 Unauthorized disclosure, G2 Material deception, G3 Harassment / intimidation / abuse, G4 Unauthorized commitment, G5 Rights request ignored, G6 Vulnerability mishandled, G7 Calling-window violation, G8 Client-designated disclosure gate, G9 Client-prohibited statement gate. The app shows them in the read-only Evaluation Profile panel. | COMPLETE | None |
| B3 | Behavioural dimensions and scenarios | D1 Understanding, D2 Accuracy & Grounding, D3 Resolution Handling, D4 Commitment & Closure, D5 Fair Treatment & Adaptation, D6 Execution & Records. Collections scenarios (contract §3): pre-due, early and late delinquency, PTP, payment claims, disputes, hardship, settlement requests, callbacks, wrong party, human handoff, refusal, consequences questions. | COMPLETE | None |
| B4 | Outcome separated from agent quality | Outcome model and outcome attribution (contract §10). The verdict comes from conduct, not from whether the borrower paid. Result screen: "Outcome: did the result come the right way?" | COMPLETE | None |
| B5 | Dangerous Win / Clean Loss | Rubric tags; the engine sets them; the result screen explains both. Demo calls show a **Dangerous Win** (settlement: Critical Fail) and a **Clean Loss** (fraud dispute: Meets Bar). | COMPLETE | None |

## C. Evaluator ("build the evaluator")

| # | Requirement | Evidence | Status | What remains |
|---|---|---|---|---|
| C1 | A call can actually be evaluated [Owner summary 2] | Evaluator **B** (`evaluators/pipelines.py`): LLM extraction → evidence verifier → `SpecRuleEngine` (every MVP gate and code) → batched judgments → finalize. **A**, **A+** and **K0** exist for comparison. Provider: Gemini `gemini-3.8-flash` (`evaluators/provider_config.py`). The demo calls replay recorded model output through the **real** rule engine without a key. | IMPLEMENTED. Real Gemini **not verified** (no key in the build environment; tested against a local fake server and recorded fixtures) | Run the live check once on Railway (see "Live test" below) |
| C2 | Transcript / audio / audio + transcript [Scope] | Transcript: IMPLEMENTED. Audio + Transcript: IMPLEMENTED (the supplied transcript is judged; the audio is attached and never overrides it, SC-02). Audio only: **EXPERIMENTAL**. `gemini-3.5-transcribe` produces transcription, speaker diarization and word timestamps; an R-02 outbound-call rule names the speakers; B judges the result (`app/audio_gemini.py`, reconciliation §3.50). | COMPLETE (audio only experimental) | Non-experimental audio needs the B-06 ASR / diarization decision and the B-11 thresholds. Stated in the product. |
| C3 | Evaluability handled | Pre-checks DC-00 (role gate), DC-02 (non-conversation), truncation, reliability markers; a NOT EVALUABLE call short-circuits deterministically; PARTIAL evaluability. Checks whose thresholds await sign-off are listed as `pending_signoff`, never guessed. | COMPLETE | Pending thresholds (`ignosis-eval spec pending`) are owner decisions |
| C4 | Evidence shown | Every finding carries verified quotes (`quote_match_min` 90); the result screen highlights the cited transcript turns when a finding is selected. | COMPLETE | None |
| C5 | Attribution handled | Rubric attribution rules (contract §7): agent behaviour / perception / platform / customer-driven / indeterminate. Shown per finding; no model reasoning is shown. | COMPLETE | None |
| C6 | Uncertain / out-of-scope cases handled conservatively | INCONCLUSIVE and OUT OF SCOPE are never counted as a pass. External truth (ledger, CRM, payment) is always OUT OF SCOPE. A provider failure is EVALUATION_FAILED, never a verdict. An invalid model citation is EVALUATION_FAILED, never a 500. | COMPLETE | None |
| C7 | Actionable result | Verdict + routing tier (1 immediate review … 6 audit sample) + a "Next step" sentence + the downloadable evaluation record (JSON). | COMPLETE | None |

## D. Reliability ("demonstrate / prove evaluator reliability")

| # | Requirement | Evidence | Status | What remains |
|---|---|---|---|---|
| D1 | A benchmark | `bench-a1`, design FROZEN (contract §12): 28 full calls, 42 short items, 12 snippets, 6 red-team, 3 calibration; 92 registry entries. The DEV design is public in `bench/public/` (25 DEV case cards, hashes in `docs/freeze/`). There are 25 DEV transcript **drafts** in `bench/dev/transcripts/` (Claude-assisted, QC-clean, `human_review_pending: true`). Holdout and red-team intents are private (SC-06). | PARTIAL: DESIGNED in full; DEV drafts exist; holdout transcripts, audio and gold PENDING (B-01) | Native human review of the DEV drafts; authoring of the holdout content |
| D2 | Independent gold methodology | The gold contract is separate from evaluator output (`contracts/gold_label.py`, `derived_from_evaluator_output` is literally `false`). Mode-level gold is derived only by `golddrv/`. There is a gold freeze with hashes (`integrity/`), blind labelling (Labeler X, and Y if available) with adjudication (B-02, B-10). The evaluator cannot see gold (static and transitive import tests + runtime `ProtectedPathGuard`). | PARTIAL: DESIGNED + IMPLEMENTED; **no gold labels exist** (PENDING, B-02) | Labelling (human) |
| D3 | Holdout strategy | Split rules (contract §13); locked runs need `--confirm-holdout`, a tagged clean commit and verified hashes (P-8 / H7); blind scoring; append-only storage; opaque unit aliases so item ids never reach an evaluator (P-17). | PARTIAL: IMPLEMENTED infrastructure; **never executed** (no holdout content) | Holdout content + gold, then one locked run |
| D4 | Red-team accounted for | 6 red-team items by an independent author, written after evaluator freeze (contract §12.6, B-03). | PENDING (DESIGNED) | Independent author |
| D5 | Reliability metrics defined | `docs/spec/scoring-spec.md` (31 metric definitions, SD-01..SD-31), safety tiers compared lexicographically; implemented in `metrics/` with handcrafted fixtures; Wilson / exact intervals in `stats/`; independent scorer (`scoring/`, reads the blinded view only). | COMPLETE (IMPLEMENTED + unit-tested) | None |
| D6 | Measured reliability | `reports/dev-baseline/` (DEV ENGINEERING MEASUREMENT, intent-referenced, not gold). **Measured:** K0 keyword floor (verdict 5/18; hard-rule failures caught 0/13, as expected with empty lexicons); pre-checks agree with the design on evaluability and calling hours 18/18; 3-repetition reproducibility of the deterministic parts. **A, A+, B: not executed** (no Gemini key where the baseline was produced). | PENDING for the actual evaluator | Run `scripts/dev_gemini_baseline.sh` with `GEMINI_API_KEY` (one command). After that, human review, gold, holdout, red-team and final validation remain. |
| D7 | Honest about measured vs pending | The reliability screen separates "Development measurement" from "Final reliability validation: PENDING". K0 is labelled as a deliberately minimal floor, not the app's evaluator (audit fix). The experimental audio path is shown as excluded from reliability claims. `docs/dev-baseline.md` has Measured / Pending / Experimental sections. | COMPLETE | None |

## E. Prototype

| # | Requirement | Evidence (screen) | Status | What remains |
|---|---|---|---|---|
| E1 | A first-time user understands what to do | A "How it works" guide (5 steps, opens on first visit, dismissible), a stepper (Choose a call → Choose input type → Evaluate → Review result → Explore reliability), the Evaluate screen laid out as Step 1 / 2 / 3, and a primary CTA. | COMPLETE | None |
| E2 | Can evaluate a call | Demo calls: one click, DEMO / REPLAY. Own call: Transcript, Audio (experimental) or Audio + Transcript; live with a key, "No verdict" without one. | COMPLETE locally; live on Railway not verified | Live check |
| E3 | Can inspect why | Result: verdict → why → findings (select one to highlight its evidence) → transcript → confidence and what could not be checked → outcome → attribution → next step. | COMPLETE | None |
| E4 | Can navigate to other calls | Call library: verdict / source filters, search, keyboard-openable rows, breadcrumb back. | COMPLETE | The library is in memory and resets when the server restarts (prototype) |
| E5 | Understands evaluator reliability | "Evaluator reliability" screen (see D7). | COMPLETE | None |
| E6 | Distinguishes DEMO / REPLAY from LIVE | Source banners: LIVE EVALUATION · Provider: Google Gemini · Evaluator: gemini-3.8-flash; DEMO / REPLAY; NO VERDICT. | COMPLETE | None |
| E7 | Understands the experimental audio limitation | Mode badge EXPERIMENTAL AUDIO; banner "EXPERIMENTAL AUDIO EVALUATION · Transcription: gemini-3.5-transcribe · Evaluator: gemini-3.8-flash" and "Audio transcription and speaker attribution are not included in final reliability claims."; an "Audio reliability: Not independently calibrated" section. | COMPLETE | None |

## F. Engineering

| # | Requirement | Evidence | Status | What remains |
|---|---|---|---|---|
| F1 | Gemini integration usable | `evaluators/llm.py::GeminiClient` (generateContent, JSON mode, schema retry, transport retries, no model fallback); `app/audio_gemini.py::GeminiTranscribeClient`. Tested against a local fake server. The exact response field names for diarized words were not confirmed against Google's live API; if they differ, audio-only fails closed (NOT EVALUABLE) and logs only field names. | IMPLEMENTED; **real API not verified** | Live check on Railway |
| F2 | API key handled safely | Runtime env `GEMINI_API_KEY` only, read in one place; sent in a header, never in a URL; redacted from errors; never in responses, logs, records, the browser or the build (`tests/test_secrets.py`, `tests/test_app.py`). | COMPLETE | None |
| F3 | Railway deployment configured | Root `Dockerfile`, `$PORT`, healthcheck `/api/health`, variables `GEMINI_API_KEY` / `GEMINI_MODEL` / `GEMINI_TRANSCRIBE_MODEL` (`docs/railway-deploy.md`). | COMPLETE (configuration); deployed state **not verified** from the build environment | Confirm Railway deployed the latest commit |
| F4 | Errors handled | Upload validation (format, size, malformed, empty) with readable 4xx messages; provider failures → EVALUATION_FAILED; evaluator defects → EVALUATION_FAILED, never a 500; no verdict without a key. | COMPLETE | None |
| F5 | Deployable | The Docker image runs `python -m ignosis_eval.app`; runs locally; 670 tests pass. | COMPLETE | None |
| F6 | Clear run / deploy path | README (commands), `docs/railway-deploy.md` (step by step), `scripts/railway_live_check.py`. | COMPLETE | None |

---

## Summary table

| Assignment Deliverable | Status | Evidence | Remaining |
|---|---|---|---|
| 1. Define what makes a good / bad call without an existing rubric | **COMPLETE** | `docs/spec/rubric.yaml` (1.2-mvp: 9 hard gates, 6 dimensions, 27 codes), `docs/spec/frozen-contract.md` §1–§10, `docs/spec/profile.yaml`; Evaluation Profile panel in the app | Profile values are a demonstration profile (B-11 sign-off); not a blocker |
| 2. Build the evaluator | **COMPLETE** (live model not yet verified) | Evaluator B (`evaluators/`, `engine/`, `pipeline/`), A / A+ / K0 for comparison, review app (`app/`), 670 tests | One live check on Railway |
| 3. Demonstrate / prove evaluator reliability | **PARTIAL** | Frozen benchmark design `bench-a1`, independent gold methodology, holdout / red-team protocol, 31 executable metric definitions, independent scorer, DEV engineering measurement (K0 + pre-checks), honest reliability screen | Real Gemini DEV run of A / A+ / B (one command, needs the key); human transcript review; gold labels; holdout; red-team; final validation |
| Three input modes (audio, transcript, audio + transcript) | **COMPLETE** (audio only is experimental) | Evaluate screen; `app/service.py`, `app/audio_gemini.py` | B-06 / B-11 for non-experimental audio |
| Working prototype | **COMPLETE** | Evaluate → Result → Call library → Evaluator reliability; demo calls; synthetic sample recording | None |
| Deployment | **COMPLETE** (configuration); **NOT VERIFIED** (live) | `Dockerfile`, `docs/railway-deploy.md`, `scripts/railway_live_check.py` | Redeploy and run the live check |

**Verdict: SUBMISSION READY WITH LIMITATIONS.** The first two deliverables are met. The third is met as a rigorous
methodology with working infrastructure; its measurements are honestly pending. The limitations below must be
stated, and the product already states them.

---

## What to show in the submission

**Product story (screens):**
1. The Evaluate screen on first visit: the "How it works" guide, Steps 1–3, the three input modes.
2. The Settlement demo (Critical Fail · Dangerous Win): verdict → why → select G4 → the highlighted turn 7 →
   "Agent promises to verify" → routing tier 1.
3. The Fraud-dispute demo (Meets Bar · Clean Loss) and the Voicemail demo (Not Evaluable): the evaluator abstains
   instead of guessing.
4. A LIVE transcript evaluation on Railway (after the live check).
5. The sample recording under Audio, with the EXPERIMENTAL AUDIO banner and the Audio reliability section.
6. The Evaluator reliability screen: development measurement vs final validation PENDING.

**Technical story (repository):**
- `engine/code_rules.py` + `evaluators/judgement.py::SpecRuleEngine`: every MVP gate and code as deterministic rules
  over verified extraction events; the LLM extracts and answers narrow judgments, and the rules decide.
- `engine/finalize.py`: the evidence verifier, confidence cap, attribution, verdict, tags and routing.
- `pipeline/prechecks.py`: evaluability and abstention before any model call.
- `evaluators/provider_config.py` + `llm.py`: one place for the provider; pinned model, no fallback, fail closed.
- `tests/test_architecture_boundaries.py`: the evaluator cannot import gold; the scorer cannot import the evaluator.

**Reliability story (artifacts):**
- `docs/spec/scoring-spec.md` + `metrics/` + `docs/reliability.md`: metric definitions as executable, tested code.
- `docs/spec/experiment-protocol.md` + `runner/`: k=5 repetitions, locked holdout runs, blinding, append-only
  results.
- `bench/public/` + `docs/freeze/`: the frozen DEV design with hash commitments; holdout intents kept private.
- `integrity/`, `golddrv/`: gold freeze and derivation, never from evaluator output.
- `reports/dev-baseline/` + `docs/dev-baseline.md`: the first engineering measurement, labelled as such.

**Limitation story (say it plainly):**
- The 25 DEV transcripts are **drafts** (Claude-assisted, QC-clean) awaiting native Hindi / Hinglish human review.
  They are DEV only and never holdout.
- **No gold labels, no holdout run, no red-team run yet.** Final reliability validation is PENDING. No number shown
  is evidence that the evaluator works.
- The DEV measurement so far covers the keyword floor and the deterministic pre-checks. The model evaluator's DEV run
  is pending the Gemini key (one command).
- **Audio only is experimental**: transcription and speaker attribution are not calibrated (B-06 / B-11 pending) and
  are excluded from all reliability claims. Audio + Transcript judges the supplied transcript.
- The profile is a demonstration profile, not Ignosis policy. Several thresholds await sign-off, and the checks that
  need them report INCONCLUSIVE instead of guessing.
- The prototype has no authentication and an in-memory call library; do not upload real customer calls to a public
  deployment.

---

## Live test (Railway)

Not verified from the build environment. The Gemini API itself is reachable from it (an unauthenticated request gets Google's own "API key required" 403), but no key is configured there: a key pasted into chat is not used, so rotate any key that was pasted. The Railway URL is not reachable from it. To let a future cloud session run the real checks, add `GEMINI_API_KEY` as an environment variable in the cloud environment's settings (a new session picks it up); otherwise run the steps below yourself.

1. Open `https://ignosiseval-production.up.railway.app/api/health`.
   Expect: `{"status":"ok","version":"0.4.0","live_evaluation":true,"models":{"evaluator":"gemini-3.8-flash","transcription":"gemini-3.5-transcribe"}}`.
   `live_evaluation: false` means the key is missing or a model id is invalid.
2. Confirm the response above.
3. Open `https://ignosiseval-production.up.railway.app/`. Expect the badge **Live evaluation on · Google Gemini ·
   gemini-3.8-flash**.
4. Step 1: on any demo card click **Open in step 2**. Step 2: select **Transcript**.
5. Under "How should this demo call be evaluated?" choose **Live evaluation with gemini-3.8-flash** (replay off).
6. Step 3: **Evaluate call** (10 to 60 s).
7. Expect **LIVE EVALUATION · Provider: Google Gemini · Evaluator: gemini-3.8-flash**, a verdict, findings with
   highlighted evidence, and no DEMO / REPLAY label. If you see "Evaluation Failed" naming the model, fix
   `GEMINI_MODEL`.
8. **← Evaluate another call** → Step 2 **Audio** → **Use the sample recording** → **Live (experimental)** →
   **Evaluate call** (20 to 90 s). Expect **EXPERIMENTAL AUDIO EVALUATION · Transcription: gemini-3.5-transcribe ·
   Evaluator: gemini-3.8-flash** and the Audio reliability section. You will then see either a verdict with speakers
   named (spk_1 → agent, spk_2 → borrower), or "Not assigned (no guess)" → NOT EVALUABLE. The second is correct fail-closed
   behaviour if diarization gives no clear two-speaker split. "Evaluation Failed" naming the transcription model
   means you should fix `GEMINI_TRANSCRIBE_MODEL`.
9. **← Evaluate another call** → Step 2 **Audio + Transcript** → **Use the sample recording** (it fills the
   transcript) → **Live evaluation** → **Evaluate call**. Expect **LIVE EVALUATION**, no experimental banner, and a
   verdict (this demo is built to illustrate a Critical Fail: the agent settles on the spot, G4; a live model may judge differently).
10. Record for each step: the banner text, the verdict, the time taken, and any "Evaluation Failed" message.

The same checks from any machine with Python 3 (no key needed there):

```bash
python scripts/railway_live_check.py https://ignosiseval-production.up.railway.app
```

Every line should read PASS.

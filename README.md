# Ignosis Voice AI Quality Evaluator: reliability-experiment infrastructure

This repository holds the infrastructure for a pre-registered reliability experiment on a quality evaluator for
collections voice-AI calls. It implements the **frozen specification pack** in [`docs/spec/`](docs/spec/):

- contract `1.2.0-frozen` (AJ-01..AJ-12 plus the Stage-5 adjudications SC-01..SC-08, BD-01, BD-02);
- rubric `1.2-mvp`;
- profile `collections_default_v1` `1.1.1`;
- experiment protocol;
- scoring specification;
- implementation blockers.

How the implementation maps to the spec is in [`docs/spec-reconciliation.md`](docs/spec-reconciliation.md). The
freeze commitments of bench-a1 design v1.0 (13 file hashes, the private bundle commitment and the Stage-5
adjudication log) are in [`docs/freeze/`](docs/freeze/). The earlier 1.1 adjudication record is kept as history in
[`docs/history/final-adjudication-1.1.md`](docs/history/final-adjudication-1.1.md).

> **Nothing in this repository is a reliability result.** The benchmark design is frozen. The 25 DEV transcripts
> exist as unfrozen drafts in `bench/dev/transcripts/` (Claude-assisted; human review pending). The first DEV
> baseline (`reports/dev-baseline/`, `docs/dev-baseline.md`) compares against the design intent, not gold. Holdout
> transcripts, audio and all gold do not exist yet (B-01/B-02 pending). bench-a1 contains no G7 positive, so G7
> recall is not measured (BD-02). The evaluator provider is Gemini (`evaluators/provider_config.py`), live only when
> the runtime variable `GEMINI_API_KEY` is set; the locked-run pin stays B-05. The ASR is a cache replay (B-06), the
> lexicon terms are empty (B-04) and 18
> profile/rubric values are `PENDING_HUMAN_SIGNOFF`. Every locked (holdout / red-team) run is refused until those
> are resolved. Every `metrics.json` carries the SD-29 scope line and warnings.

## Status at a glance

| Layer | Status |
|---|---|
| Spec pack | `docs/spec/` (the 6 frozen 1.2.0 files, verbatim; hashes committed in `docs/freeze/FREEZE-HANDOFF.md` and verified by every `bench check`). `ignosis-eval spec pending` lists the pending items. |
| Contracts, front end, engine, K0 / A / A+ / B interfaces, gold derivation, scorer, runner, lock, blinding | Implemented and tested (`python -m pytest`). |
| B: extraction contract + verifier, full rule engine (`SpecRuleEngine`: every MVP gate and code, batched judgments) | Implemented and tested; B's default. A, A+ and B run end to end (tested on replay fixtures and a fake OpenAI-compatible server). |
| Evaluator provider | **Gemini** (`gemini-3.8-flash`, temperature 0, seed 0, JSON mode + 1 schema retry, ≤ 3 transport retries), configured in one place (`evaluators/provider_config.py`). The key is read only from the runtime env `GEMINI_API_KEY`; without it, A / A+ / B do not run and nothing is fabricated. The pin for locked runs stays B-05. A Claude evaluator is refused on the Claude-assisted drafts (constraint 1). |
| Review app (clickable MVP) | `src/ignosis_eval/app/`: guided journey (choose a call → choose input type → evaluate → review result → explore reliability) over Evaluate a call (Transcript / Audio, EXPERIMENTAL / Audio + Transcript), Result, Call library, Evaluator reliability; fictional demo calls and a synthetic sample recording; LIVE EVALUATION vs DEMO / REPLAY always labelled. FastAPI backend; the browser never sees the key. Railway-ready (`Dockerfile`). |
| Opaque unit aliases (P-17, SC-05) | Random `u_xxxxxxxx` alias per unit per run, private mapping, audio renamed before ASR, pre-run identity-leak test that fails the run (identity tokens only; ordinary words pass). |
| Deterministic normalizer, DC-02 / DC-LANG / DC-01-audio, diarization turn threshold, timing signals | Pending sign-off (see the reconciliation doc §5). |
| Frozen DEV design (Stage 5) | `bench/public/`: 25 DEV case cards (functional beats), master matrix and gold blueprint, verbatim; committed by `docs/freeze/FREEZE-public.md`; validated by `ignosis-eval bench public-check` (0 errors, 6 PD015 authoring-rule warnings; clarifications BD-03..BD-05 in `docs/bd-changelog.md`). DEV only. |
| Transcripts, gold, registries | DEV transcript **drafts** in `bench/dev/transcripts/` (25; not frozen; `bench transcript-qc` clean). Gold and registries are empty (B-02); the blueprint is intent, not gold. |
| Results | **None.** The DEV baseline (`reports/dev-baseline/`) is a DEV ENGINEERING MEASUREMENT (K0 only until the key is set), not reliability evidence. FINAL RELIABILITY VALIDATION: PENDING. |

## Quick start

```bash
pip install -e ".[dev]"
python -m pytest                         # the whole suite
ruff check src tests scripts && mypy     # lint + types
ignosis-eval spec check && ignosis-eval spec pending
ignosis-eval bench check --scope dev --require-gold
ignosis-eval bench transcript-qc <dir>          # draft DEV transcripts (<ITEM_ID>.txt) before the hash freeze
ignosis-eval dev run --systems K0 && ignosis-eval dev report --run-id <id>   # DEV draft baseline (docs/dev-baseline.md)
ignosis-eval dev smoke                         # live Gemini check (needs GEMINI_API_KEY in the environment)
scripts/dev_gemini_baseline.sh                 # K0, A, A+, B on the 18 scored DEV calls + DEV report
python -m ignosis_eval.app                    # review app on http://localhost:8000 (needs the [web] extra; PORT honoured)
```

A full dev run once items, cards and gold exist (plumbing only while the backend is a mock):

```bash
ignosis-eval bench manifest --scope dev --dataset-version v1 --created-by <id>
ignosis-eval gold freeze    --scope dev --gold-version g1 --frozen-by <id> --labeling-protocol-version <v>
ignosis-eval run   --split dev --systems K0,A,A+,B --base-seed 1234 --replay-dir <replay fixtures>
ignosis-eval blind --run-id <run_id>
ignosis-eval score --run-id <run_id>     # -> scoring/<run_id>/{item_scores.csv, metrics.json, discordance_tables.csv, human_checks.csv}
```

## Review app (clickable MVP)

For the AI Quality Reviewer / Operations QA Reviewer: *Ignosis already listens to every call. This layer judges
whether the AI agent behaved correctly.*

The journey is **1 Choose a call → 2 Choose input type → 3 Evaluate → 4 Review result →
5 Explore reliability**. The Evaluate screen is laid out as steps 1–3; a progress stepper shows all five. A compact
**How it works** guide opens on the first visit, can be dismissed (remembered in the browser only) and reopened
from the header.

| Screen | What it does |
|---|---|
| Evaluate a call | **Step 1 · Choose a call.** Five fictional demo calls, each labelled with what it illustrates (Critical Fail / Dangerous Win, Needs Attention, Meets Bar, Clean Loss, Not Evaluable); *View evaluation* opens the replayed result in one click, *Open in step 2* loads it. **Step 2 · Choose input type.** **Transcript** ("Paste or upload a transcript"). **Audio** ("Upload a call recording", badge **EXPERIMENTAL AUDIO**): transcribed by `gemini-3.5-transcribe`, judged by `gemini-3.8-flash`, see below. **Audio + Transcript** ("Upload both for the strongest available evaluation"): the transcript is judged and the recording is attached; audio never overrides the transcript (SC-02). **Step 3 · Evaluate.** |
| Result | Continues the Evaluate journey (step 3, with *Evaluate another call*). Order: source banner (LIVE EVALUATION / DEMO / REPLAY / NO VERDICT) → verdict and what it means → why (the primary finding with its first quote) → findings (click one to highlight its transcript turns) → evidence transcript (cited turns and agent promises highlighted) → confidence and what could not be checked (EVALUABLE / PARTIAL / NOT EVALUABLE; INCONCLUSIVE and OUT OF SCOPE checks; facts that need external truth such as payment status) → outcome (Dangerous Win, Clean Loss, each explained) → attribution (no model reasoning) → next step and routing. A failed or unavailable evaluation shows what ran and never shows a verdict. Side panels: verdict scale, evaluator details, the read-only Evaluation Profile, the evaluation record as JSON. |
| Call library | Demo calls and calls evaluated since the server started (in memory; DEV / demo only). Filter by verdict and source, search by name or finding; each row opens the result (mouse or keyboard). |
| Evaluator reliability | **Development measurement** (what was tested, what is measured so far, what has not run and why) kept separate from **Final reliability validation: PENDING** (what it needs). Metrics carry plain-language help; technical details stay available. Reads only `reports/dev-baseline/`; no gold, no holdout. |

**EXPERIMENTAL audio-only evaluation** (owner-authorized prototype path, `app/audio_gemini.py`). Two models, two
jobs: `GEMINI_TRANSCRIBE_MODEL` (`gemini-3.5-transcribe`) transcribes the uploaded .wav / .mp3 (≤ 14 MB, sent inline
without its file name) with speaker diarization and word timestamps; `GEMINI_MODEL` (`gemini-3.8-flash`, Evaluator B)
then judges the turns through the existing front end, rule engine and record, exactly like a transcript. The
transcription model never judges and the evaluator never hears the audio.
- **Speaker roles by rule, never guessed** (R-02: no LLM role fallback). Diarization labels speakers anonymously
  (`spk_1`, `spk_2`). With exactly two speakers, the one who opens the call and also speaks the most words is the
  agent. Anything else (no labels, one speaker, three or more, or the two criteria disagreeing, e.g. the borrower
  answers "Hello?" first) assigns no role, and the call is NOT EVALUABLE (ROLE_UNCERTAIN). No confidence score is
  invented.
- **Timing** comes from the word timestamps when every turn has them (timing checks then run).
- **Labels.** Every such result carries **EXPERIMENTAL AUDIO EVALUATION**, "Transcription: gemini-3.5-transcribe ·
  Evaluator: gemini-3.8-flash", "Audio transcription and speaker attribution are not included in final reliability
  claims." and **Audio reliability: Not independently calibrated**.
- **Scope.** Not the B-06 ASR / diarization decision and not calibrated against B-11. App only: never a benchmark
  run, gold or a reliability metric (`test_experimental_audio_path_stays_in_the_review_app`). Without a key: no
  verdict (the sample recording still shows its recorded demo evaluation).

**Sample recording.** `demo-settlement` ships with a fictional recording (`app/static/demo/demo-settlement.wav`,
56 s, 8 kHz mono) spoken by espeak-ng synthetic voices from that demo's transcript, so the audio and the transcript
correspond one to one. No real person's voice is used. On the form, *Use the sample recording* attaches it in
either audio mode: its recorded demo evaluation (DEMO / REPLAY, Critical Fail) without a key, or a live evaluation
when a key is configured (Audio only: transcribed by `gemini-3.5-transcribe`, then judged; Audio + Transcript: its transcript is judged). Regenerate with `python scripts/make_demo_audio.py` (needs `espeak-ng`
at generation time only).

Every result is labelled with its source:
- **LIVE EVALUATION**: Evaluator B with Gemini, server side.
- **DEMO / REPLAY**: five synthetic demo calls whose scripted model output is replayed through the real rule engine;
  it works without a key.
- **EXPERIMENTAL AUDIO EVALUATION** (added to LIVE or DEMO / REPLAY for audio-only uploads): Gemini listened to the
  recording; audio transcription and speaker attribution are not independently calibrated.
- **NO VERDICT** (evaluator unavailable): no key on the server. The front end still runs, but no verdict is shown, and nothing is
  ever counted as a pass.

Run it locally:

```bash
pip install -e ".[web]"
export GEMINI_API_KEY=...        # optional; in your shell or an untracked .env loader, never in a file in git
python -m ignosis_eval.app       # http://localhost:8000
```

## Deploy on Railway

Exact steps: [`docs/railway-deploy.md`](docs/railway-deploy.md). After deploying, run
`python scripts/railway_live_check.py https://<your-service>.up.railway.app` to check transcript, audio-only, audio +
transcript and demo replay end to end (the manual 12-step browser sequence is in the deploy doc, §3E). Railway builds the repository `Dockerfile`; the
image runs `python -m ignosis_eval.app` on `0.0.0.0:$PORT`. Set the healthcheck path to `/api/health`.

### Railway Variables

| Variable | Required | Secret? | Value |
|---|---|---|---|
| `GEMINI_API_KEY` | yes | yes (seal it in Railway; never put it in any file) | your Gemini API key |
| `GEMINI_MODEL` | yes | no | `gemini-3.8-flash`: the evaluator (also the code default) |
| `GEMINI_TRANSCRIBE_MODEL` | yes | no | `gemini-3.5-transcribe`: audio-only transcription and speaker diarization (also the code default) |
| `PORT` | no | no | Railway provides it; do not set it |

- **Runtime only.** All three variables are read from the environment at runtime, never at build time. The
  Dockerfile has no `ARG`.
- **Key stays on the server.** Gemini is called only from the server. The key never reaches the browser, API
  responses, logs, records or reports.
- **Exact models, no fallback.** An unavailable model fails clearly, with an error naming `GEMINI_MODEL` or
  `GEMINI_TRANSCRIBE_MODEL`, and the app never switches models. No Live (streaming) model is used.
- **No key.** Without a key the app runs in DEMO / REPLAY mode, and custom calls show EVALUATOR UNAVAILABLE.
- **No authentication.** Do not upload real customer calls to a public deployment.

## Repository layout

```
docs/spec/          frozen specification pack (authoritative, 1.2.0-frozen)
docs/freeze/        freeze commitments (hashes, private bundle commitment, Stage-5 adjudication log)
bench/              frozen DEV design (public/), dev items (empty), hash lists, registries, card template
src/ignosis_eval/
  spec/             spec loader, check registry from rubric.yaml, PENDING inventory
  contracts/        NormalizedInput, EvaluationRecord, GoldLabel, CaseCard, ItemMeta, Registries, manifests
  pipeline/         shared front end: transcript intake, ASR cache, normalization, lexicon, pre-checks, evaluability
  engine/           deterministic verifier, confidence ceiling, attribution, verdict engine, tags, routing
  evaluators/       K0, A, A+ (derived), B, provider config + Gemini / OpenAI-compatible clients, replay mock, prompts
  app/              review app: FastAPI backend (service.py, server.py), static SPA, synthetic demo calls
  golddrv/          independent mode-level gold derivation (capability table, attribution)
  metrics/ scoring/ stats/   scoring-spec SD-01..SD-31 on the blinded view
  benchmark/ integrity/      layout, card rules, bench checks, locked-run registry; hash lists, gold freeze, guard
  runner/           P-5/P-6 run protocol, lock (P-8), blinding (P-10), append-only storage (P-11)
tests/              synthetic-stub tests only (P-1 rule 3)
```

## Documentation

- [`docs/spec-reconciliation.md`](docs/spec-reconciliation.md): what changed and why; conventions; open questions.
- [`docs/data-contracts.md`](docs/data-contracts.md), [`docs/benchmark-authoring.md`](docs/benchmark-authoring.md),
  [`docs/experiment-protocol.md`](docs/experiment-protocol.md), [`docs/reliability.md`](docs/reliability.md):
  implementation maps to the spec.
- [`docs/dev-baseline.md`](docs/dev-baseline.md): DEV draft runs, the intent reference, how to run A / A+ / B.
- [`docs/railway-deploy.md`](docs/railway-deploy.md): Railway deployment and variables.
- [`docs/gap-analysis.md`](docs/gap-analysis.md): history of the infrastructure phase and its reconciliation.

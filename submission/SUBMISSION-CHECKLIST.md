# Submission checklist: Ignosis Voice AI Quality Evaluator

| | |
|---|---|
| **Prototype URL** | https://ignosiseval-production.up.railway.app. Its live state was **not verified** from the build environment, where the URL is unreachable. Verify it with the steps at the bottom. |
| **GitHub repository** | https://github.com/aaryaoganja/Ignosis_eval, branch `spec/frozen-stage4`. The last code change is commit `ae77adb`; this folder adds documents only. |
| **Deliverables (this folder)** | `Ignosis_Voice_AI_Quality_Evaluator_Final.pdf` (10 pages) · `DEMO-SCRIPT.md` (6–7 min) · `INTERVIEW-QA.md` · this checklist · `source/` (deck HTML and the real app screenshots used in it) |

## What is live in the prototype

- **Evaluate a call.** Transcript, Audio + Transcript, and Audio only, which is labelled **EXPERIMENTAL AUDIO
  EVALUATION**.
  - Evaluator B uses Google Gemini `gemini-3.8-flash`; audio transcription uses `gemini-3.5-transcribe`.
  - Live evaluation needs `GEMINI_API_KEY` set on the server. Without it, the app shows **Demo mode** and your own
    calls get the pre-checks only, labelled **NO VERDICT**.
- **Five fictional demo calls.** Critical Fail + Dangerous Win, Needs Attention, Meets Bar, Meets Bar + Clean Loss,
  and Not Evaluable. Each is replayed through the real rules engine and labelled **DEMO / REPLAY**. One includes a
  synthetic sample recording.
- **Result view.** Verdict, why, findings with evidence tied to turns, what could not be checked, Dangerous Win /
  Clean Loss, attribution, next step and review tier, and a JSON record download.
- **Call library** (in memory) and an **Evaluator reliability** screen.

## What is measured

Source: DEV engineering run `devdraft-20260929T134347200882Z`, on 18 draft DEV calls compared with each call's design
intent. These are not gold labels.

| Measured | Result |
|---|---|
| Evaluability pre-check vs intent | 18 / 18 |
| Calling-window check (G7) vs intent | 18 / 18 |
| Repeatability of the deterministic parts (pre-checks, K0) | 3 identical runs |
| K0 keyword floor | verdict 5 / 18; hard-rule recall 0 / 13. By design: its lexicon is empty until native review |
| Engineering checks | 670 tests pass, 1 skipped; ruff and mypy clean |

## What is pending

- The live DEV run of model evaluators A, A+ and B. Not executed: no provider key in the build environment.
- Native Hindi / Hinglish review of the 18 DEV transcripts.
- Independent, blind gold labels.
- The locked holdout run.
- The red-team run.
- Model repeatability over 5 repetitions.
- Sign-off of the 18 `PENDING_HUMAN_SIGNOFF` profile values.
- Calibration of audio transcription and speaker attribution.
- A live check of the Railway deployment.

## Known limitations

- There is **no accuracy number for the evaluator in the app**, and none is claimed.
- The DEV transcripts are Claude-assisted drafts, still pending human review.
- The live Gemini path is built and tested end to end only against a simulated provider. The exact field names of
  Gemini's diarized transcription response are unconfirmed; if they differ, audio-only fails closed as Not
  Evaluable.
- Audio-only is experimental and excluded from every reliability claim. With Audio + Transcript, the transcript is
  the primary source.
- `collections_default_v1` is a demonstration profile, **not Ignosis policy**.
- Payment, ledger, CRM and authority truth are unavailable, so those 13 checks are always out of scope. Agent promises
  are listed for follow-up, not judged.
- The call library is in memory. There is no authentication, persistence or Ignosis integration; these are out of the
  assignment's scope.

## Before you submit (owner actions)

1. **Rotate the Gemini key** that was pasted into chat. Set the new one only as the Railway variable
   `GEMINI_API_KEY`, next to `GEMINI_MODEL=gemini-3.8-flash` and `GEMINI_TRANSCRIBE_MODEL=gemini-3.5-transcribe`.
2. Redeploy the latest commit of `spec/frozen-stage4`, then open `/api/health`. It should report both models, and the
   badge should read **Live evaluation on**.
3. Run `python scripts/railway_live_check.py https://ignosiseval-production.up.railway.app`, or follow the manual test
   sequence in `docs/railway-deploy.md` (§3, step 12).
4. Optional: replace the page 6 screenshots with live ones. Every screenshot in the PDF today is DEMO / REPLAY, from
   a local run of commit `ae77adb`.

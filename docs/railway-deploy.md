# Deploy the review app on Railway, and run it locally with Gemini

The repository deploys as **one Railway service** built from the root `Dockerfile`. There is no database, volume,
cron job or second service. Railway's `railway.json` / `railway.toml` (Config as Code) is deprecated and new
services cannot use it, so the few settings below are set once in the Railway dashboard.

## 1. What the repository already handles

| Concern | Handled by |
|---|---|
| Build | `Dockerfile` at the repository root (Python 3.11 slim). It installs the package with the `[web]` extra and copies only the package, the frozen spec pack and the committed DEV report. No benchmark items, drafts, gold, runs or tests enter the image. |
| Start | Image `CMD ["python", "-m", "ignosis_eval.app"]`. It serves on `0.0.0.0:$PORT` (default 8000 when `PORT` is unset). A test covers this. |
| Health | `GET /api/health` returns HTTP 200 `{"status":"ok","version":...,"live_evaluation":true/false,"models":{"evaluator":"gemini-3.8-flash","transcription":"gemini-3.5-transcribe"}}` (model ids only, never the key). |
| Secrets | `GEMINI_API_KEY` is read at runtime by the server only (`evaluators/provider_config.py`). The Dockerfile declares no `ARG`, so Railway passes no variable into the build and no build layer contains the key. `.dockerignore` excludes `.env*`. The key is sent only in the `x-goog-api-key` header to Google and is redacted from errors. It never appears in responses, logs, records or reports (tests: `test_secrets.py`, `test_app.py`). |
| Models | Two models, each used exactly as given, with no fallback. `GEMINI_MODEL` (the evaluator: extraction, judgments) and `GEMINI_TRANSCRIBE_MODEL` (used ONLY by the experimental audio-only path: speech-to-text, speaker diarization, word timestamps; a Live/streaming model is refused). `latest` / `preview` / `exp` aliases are refused. An unavailable model makes the evaluation "Evaluation Failed" with a message naming the model and its variable; an invalid id makes it "no verdict" before any call. Each record stores the evaluator provider (`system.llm_backend`) and model (`system.model_snapshot_id`); audio-only results also name the transcription model. |
| Without a key | The app still starts and is healthy. Demo calls run as **DEMO / REPLAY**. Custom calls show **EVALUATOR UNAVAILABLE** ("GEMINI_API_KEY is not set"), never a verdict. |
| Failures | Timeouts, rate limits, 5xx, invalid or blocked output all become **Evaluation Failed**, never a pass. |

## 2. Railway variables

| Variable | Required | Secret? | Value |
|---|---|---|---|
| `GEMINI_API_KEY` | yes | **yes** | your Gemini API key (from Google AI Studio). Add it as a sealed variable. |
| `GEMINI_MODEL` | yes | no | `gemini-3.8-flash` (the evaluator) |
| `GEMINI_TRANSCRIBE_MODEL` | yes | no | `gemini-3.5-transcribe` (audio-only transcription and speaker diarization) |
| `PORT` | no | no | Railway provides it. Do not set it. |

No other variable is needed. `GEMINI_BASE_URL` exists only for a proxy or a test double; leave it unset. Do not set
`HOST`; the default `0.0.0.0` is correct.

## 3. Railway: what you do (step by step)

**A. GitHub connection (one time)**
1. Sign in at railway.com with the account that will own the project.
2. If your Railway account is not yet linked to GitHub, link it. Railway asks during the first "Deploy from GitHub
   repo". You can also install the Railway GitHub App at https://github.com/apps/railway-app/installations/new.
3. In the GitHub App installation, give it access to the repository **`aaryaoganja/Ignosis_eval`**: either all
   repositories, or "Only select repositories" with this one added. If GitHub shows pending permission updates for
   the Railway app, accept them.
4. Automatic deploys on push need at least one Railway project member whose GitHub account has contributor access
   to that repository. If a push comes from a GitHub user without a linked Railway account, Railway holds it as a
   **Deployment Approval** until you click Approve.

**B. Create the service**

5. Railway dashboard → **New Project → Deploy from GitHub repo** → choose **`aaryaoganja/Ignosis_eval`**.
6. Open the new service → **Settings → Source**:
   - branch: **`spec/frozen-stage4`**;
   - Root Directory: leave empty (the repository root, where the `Dockerfile` is);
   - automatic deploys on push to that branch are on by default; leave them on.
7. **Settings → Build**: nothing to set. Railway detects the root `Dockerfile` and builds with it. Leave the build
   command and Dockerfile path empty.
8. **Settings → Deploy**: leave the start command empty (the image's `CMD` starts the server). Set
   **Healthcheck Path** to `/api/health`. The default healthcheck timeout (300 s) is fine: the app is healthy
   within seconds.

**C. Variables**

9. Service **Variables** tab:
   - **New Variable** `GEMINI_API_KEY` = your key, then seal it (the "Seal" option in the variable's menu);
   - **New Variable** `GEMINI_MODEL` = `gemini-3.8-flash`;
   - **New Variable** `GEMINI_TRANSCRIBE_MODEL` = `gemini-3.5-transcribe`;
   - do not add `PORT`.

**D. Deploy and expose**

10. Click **Deploy**, or **Apply changes** if Railway shows staged changes. Wait for the build and the healthcheck.
11. **Settings → Networking → Public Networking → Generate Domain**. Railway detects the port the app listens on
    (the `PORT` it injected) and sets it as the target port. If it asks, pick the single detected port. Nothing else
    is needed to expose the app.

**E. Check it**

12. Run this one manual test sequence on the deployed app (all content is fictional; `<app>` =
    `https://ignosiseval-production.up.railway.app`):

    1. Open `<app>/api/health`. Expect `"status":"ok"`, `"live_evaluation":true` and
       `"models":{"evaluator":"gemini-3.8-flash","transcription":"gemini-3.5-transcribe"}`. If `live_evaluation` is
       `false`, the key is missing or a model id is invalid.
    2. Open `<app>/`. The top-right badge reads **Live evaluation on · Google Gemini · gemini-3.8-flash**.
    3. Step 1: on any demo card click **Open in step 2**. Step 2: select **Transcript**.
    4. Turn replay OFF: under "How should this demo call be evaluated?" choose **Live evaluation with
       gemini-3.8-flash** (not "Recorded demo evaluation").
    5. Step 3: click **Evaluate call** (10 to 60 s).
    6. Confirm the result banner reads **LIVE EVALUATION · Provider: Google Gemini · Evaluator: gemini-3.8-flash**
       (never DEMO / REPLAY) and that a verdict is shown.
    7. Click **← Evaluate another call**. Step 2: select **Audio** (badge EXPERIMENTAL AUDIO).
    8. Click **Use the sample recording**, then choose **Live (experimental): transcribed by gemini-3.5-transcribe,
       evaluated by gemini-3.8-flash**, then **Evaluate call** (20 to 90 s).
    9. Confirm the **EXPERIMENTAL AUDIO EVALUATION** banner with **Transcription: gemini-3.5-transcribe · Evaluator:
       gemini-3.8-flash** and "Audio transcription and speaker attribution are not included in final reliability
       claims.", a verdict, and the **Audio reliability** section (speaker attribution: spk_1 → agent, spk_2 →
       borrower; or "Not assigned (no guess)", which makes the call NOT EVALUABLE by design).
    10. Click **← Evaluate another call**. Step 2: select **Audio + Transcript**, click **Use the sample recording**
        (it also fills the transcript), choose **Live evaluation**.
    11. Click **Evaluate call**.
    12. Confirm **LIVE EVALUATION · Provider: Google Gemini · Evaluator: gemini-3.8-flash**, no experimental banner
        (the supplied transcript is judged; the recording is attached), and a verdict.

    Demo calls (Step 1 → **View evaluation**) must show **DEMO / REPLAY**. If a result shows "Evaluation Failed" and
    names a model, set that model's variable to a model your key can use and redeploy. Failures never show a verdict.

13. Or run the same checks from any machine with Python 3 (no key needed on that machine: the key stays in
    Railway):

    ```bash
    python scripts/railway_live_check.py https://ignosiseval-production.up.railway.app
    ```

    It checks health, both model ids and that live evaluation is configured, then runs, with fictional content only:
    - **Transcript, LIVE**: request → Gemini → extraction and judgments accepted → evaluation record created;
      provider `gemini` and model `gemini-3.8-flash` recorded;
    - **Audio only, LIVE, EXPERIMENTAL**: the sample recording → `gemini-3.5-transcribe` → role rule → B on
      `gemini-3.8-flash` → record, labelled EXPERIMENTAL AUDIO EVALUATION and naming both models;
    - **Audio + Transcript, LIVE**: record created, unit A+T;
    - **DEMO / REPLAY** still labelled apart; a failure is never a verdict.

    Every line should read PASS. The script prints each verdict; they are plumbing checks on fictional calls, not
    reliability results.

Upload limits: Transcript 256 KB; Audio only .wav / .mp3 up to 14 MB (sent inline to the transcription model,
which keeps the whole request under the 20 MB inline limit; the Files API is not used, so the recording is not
stored at Google for later requests); Audio + Transcript .wav / .mp3 / .m4a up to 25 MB (fingerprinted only).
Nothing uploaded is written to disk. A live audio evaluation usually takes 20 to 90 seconds (one transcription
request, 180 s timeout, then B's calls on the evaluator model).

Later pushes to `spec/frozen-stage4` redeploy automatically. Changing a variable needs a redeploy (Railway stages
it; click Deploy).

## 4. Gemini connectivity on Railway

- There is **no separate "Gemini connection"** in Railway, and no Railway integration or plugin to add. Setting
  `GEMINI_API_KEY` (and `GEMINI_MODEL`, `GEMINI_TRANSCRIBE_MODEL`) on the service is sufficient.
- The server makes outbound HTTPS calls to
  `https://generativelanguage.googleapis.com/v1beta/models/<GEMINI_MODEL>:generateContent` (and, for audio-only,
  `.../models/<GEMINI_TRANSCRIBE_MODEL>:generateContent`). The key goes in the
  `x-goog-api-key` header, never in the URL.
- Railway services can make outbound internet requests by default. No networking configuration, private
  networking or static outbound IP is required. (Static outbound IPs are a paid Railway feature, needed only if
  your Google project restricts the key by IP; it is not needed by default.)
- Google-side prerequisites are yours: a Gemini API key from Google AI Studio for a project with the Generative
  Language API enabled, and access to the models you set in `GEMINI_MODEL` and `GEMINI_TRANSCRIBE_MODEL`.

## 5. Run locally with Gemini

Needs Python 3.11+, git and a shell.

```bash
git clone https://github.com/aaryaoganja/Ignosis_eval.git
cd Ignosis_eval
git checkout spec/frozen-stage4
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"                 # includes the web extra and the test tools

read -rs GEMINI_API_KEY && export GEMINI_API_KEY   # paste the key, press Enter (kept out of shell history)
export GEMINI_MODEL=gemini-3.8-flash
export GEMINI_TRANSCRIBE_MODEL=gemini-3.5-transcribe

ignosis-eval dev smoke                  # live check: request, parsing, extraction + judgment schemas, records
python -m ignosis_eval.app              # the app on http://localhost:8000 (LIVE EVALUATION badge)
```

Real DEV measurement: K0, A, A+ and B on the 18 scored DEV calls, then the DEV report. It is DEV only (no
holdout, gold or red team) and not reliability evidence.

```bash
scripts/dev_gemini_baseline.sh          # smoke -> dev run -> reports/dev-baseline/dev-baseline.{md,json}
CONSISTENCY_REPS=3 scripts/dev_gemini_baseline.sh   # optional: adds a repeated-run consistency run (3x the calls)
git add reports/dev-baseline && git commit -m "DEV baseline with Gemini" && git push   # the report only; runs stay local
```

Run the same image locally with Docker (optional):

```bash
docker build -t ignosis-review .
docker run --rm -p 8000:8000 -e GEMINI_API_KEY -e GEMINI_MODEL=gemini-3.8-flash \
  -e GEMINI_TRANSCRIBE_MODEL=gemini-3.5-transcribe ignosis-review
# -e GEMINI_API_KEY with no value passes the variable from your shell; the key is not typed on the command line
```

There is no authentication. Use DEV or demo calls only, and do not upload real customer calls to a public
deployment.

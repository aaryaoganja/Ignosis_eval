# Deploy the review app on Railway, and run it locally with Gemini

The repository deploys as **one Railway service** built from the root `Dockerfile`. There is no database, volume,
cron job or second service. Railway's `railway.json` / `railway.toml` (Config as Code) is deprecated and new
services cannot use it, so the few settings below are set once in the Railway dashboard.

## 1. What the repository already handles

| Concern | Handled by |
|---|---|
| Build | `Dockerfile` at the repository root (Python 3.11 slim). It installs the package with the `[web]` extra and copies only the package, the frozen spec pack and the committed DEV report. No benchmark items, drafts, gold, runs or tests enter the image. |
| Start | Image `CMD ["python", "-m", "ignosis_eval.app"]`. It serves on `0.0.0.0:$PORT` (default 8000 when `PORT` is unset). A test covers this. |
| Health | `GET /api/health` returns HTTP 200 `{"status":"ok","version":...,"live_evaluation":true/false}`. |
| Secrets | `GEMINI_API_KEY` is read at runtime by the server only (`evaluators/provider_config.py`). The Dockerfile declares no `ARG`, so Railway passes no variable into the build and no build layer contains the key. `.dockerignore` excludes `.env*`. The key is sent only in the `x-goog-api-key` header to Google and is redacted from errors. It never appears in responses, logs, records or reports (tests: `test_secrets.py`, `test_app.py`). |
| Model | `GEMINI_MODEL` is used exactly as given. `latest` / `preview` / `exp` aliases are refused, and there is no fallback. An unavailable model makes every live evaluation "Evaluation Failed", with a message naming the model and `GEMINI_MODEL`. Each record stores the provider (`system.llm_backend`) and model (`system.model_snapshot_id`); the Result screen also shows the version Google served. |
| Without a key | The app still starts and is healthy. Demo calls run as **DEMO / REPLAY**. Custom calls show **EVALUATOR UNAVAILABLE** ("GEMINI_API_KEY is not set"), never a verdict. |
| Failures | Timeouts, rate limits, 5xx, invalid or blocked output all become **Evaluation Failed**, never a pass. |

## 2. Railway variables

| Variable | Required | Secret? | Value |
|---|---|---|---|
| `GEMINI_API_KEY` | yes | **yes** | your Gemini API key (from Google AI Studio). Add it as a sealed variable. |
| `GEMINI_MODEL` | yes | no | `gemini-3.8-flash` |
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
   - do not add `PORT`.

**D. Deploy and expose**

10. Click **Deploy**, or **Apply changes** if Railway shows staged changes. Wait for the build and the healthcheck.
11. **Settings → Networking → Public Networking → Generate Domain**. Railway detects the port the app listens on
    (the `PORT` it injected) and sets it as the target port. If it asks, pick the single detected port. Nothing else
    is needed to expose the app.

**E. Check it**

12. Open `https://<your-service>.up.railway.app/api/health`. Expect `"status":"ok"` and `"live_evaluation":true`.
    If you see `false`, the key is missing, or `GEMINI_MODEL` is invalid or an alias.
13. Open `https://<your-service>.up.railway.app/`. The top-right badge should read
    **LIVE EVALUATION: gemini-3.8-flash**.
14. **Evaluate a Call → Transcript**:
    - pick a demo call, untick "Replay the scripted demo output", and press **Evaluate**;
    - the result must be labelled **LIVE EVALUATION**, and the Evaluator panel shows provider `gemini`, the model and
      the served version.
    - If it shows "Evaluation Failed" and names the model, change `GEMINI_MODEL` to a model your key can use and
      redeploy.

15. Or run the whole live check from any machine with Python 3 (no key needed on that machine: the key stays in
    Railway):

    ```bash
    python scripts/railway_live_check.py https://<your-service>.up.railway.app
    ```

    It sends one fictional demo call for a LIVE evaluation and checks:
    - health, and that live evaluation is configured;
    - request → Gemini response → extraction and judgments accepted → evaluation record created;
    - provider `gemini` and model `gemini-3.8-flash` are recorded;
    - a failure is never a verdict;
    - DEMO / REPLAY is still labelled apart.

    Every line should read PASS.

Later pushes to `spec/frozen-stage4` redeploy automatically. Changing a variable needs a redeploy (Railway stages
it; click Deploy).

## 4. Gemini connectivity on Railway

- There is **no separate "Gemini connection"** in Railway, and no Railway integration or plugin to add. Setting
  `GEMINI_API_KEY` (and `GEMINI_MODEL`) on the service is sufficient.
- The server makes outbound HTTPS calls to
  `https://generativelanguage.googleapis.com/v1beta/models/<GEMINI_MODEL>:generateContent`. The key goes in the
  `x-goog-api-key` header, never in the URL.
- Railway services can make outbound internet requests by default. No networking configuration, private
  networking or static outbound IP is required. (Static outbound IPs are a paid Railway feature, needed only if
  your Google project restricts the key by IP; it is not needed by default.)
- Google-side prerequisites are yours: a Gemini API key from Google AI Studio for a project with the Generative
  Language API enabled, and access to the model you set in `GEMINI_MODEL`.

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
docker run --rm -p 8000:8000 -e GEMINI_API_KEY -e GEMINI_MODEL=gemini-3.8-flash ignosis-review
# -e GEMINI_API_KEY with no value passes the variable from your shell; the key is not typed on the command line
```

There is no authentication. Use DEV or demo calls only, and do not upload real customer calls to a public
deployment.

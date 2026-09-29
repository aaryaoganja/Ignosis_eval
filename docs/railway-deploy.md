# Deploy the review app on Railway

The repository root holds a `Dockerfile`, and Railway builds it as is. There is no other infrastructure: no
database, no volume, no extra service. Railway's `railway.json` / `railway.toml` (Config as Code) is deprecated, so
the settings below are made once in the Railway dashboard.

## Railway variables

| Variable | Value | Secret |
|---|---|---|
| `GEMINI_API_KEY` | your Gemini API key | **yes**: seal it; never put it in any file |
| `GEMINI_MODEL` | the exact Gemini model to use (default if unset: `gemini-3.8-flash`) | no |

Railway injects `PORT` itself. Do not set it.

How the app treats these variables:
- It reads both variables at runtime only. The Dockerfile declares no `ARG`, so neither enters the build.
- Gemini is called only from the server. The key is never sent to the browser, returned by the API, logged, or
  written to records or reports.
- `GEMINI_MODEL` is used exactly as given. A `latest` / `preview` / `exp` alias is refused.
- If the model is unavailable to the key, every evaluation fails with a clear message naming the model and
  `GEMINI_MODEL` (the Result screen shows "Evaluation Failed"). The app never switches to another model.
- Each result records the provider (`system.llm_backend`) and the exact model (`system.model_snapshot_id`); the
  version the provider actually served is shown on the Result screen.
- Without `GEMINI_API_KEY` the app still works in **DEMO / REPLAY** mode (five fictional demo calls). Custom
  calls then show "EVALUATOR UNAVAILABLE" and no verdict.

## Steps

1. In Railway: **New Project → Deploy from GitHub repo**, and connect this GitHub repository
   (`aaryaoganja/Ignosis_eval`).
2. In the service **Settings → Source**, select the branch **`spec/frozen-stage4`**.
3. Leave the builder on the repository **Dockerfile**, which Railway detects at the root. There is no build
   command and no start command to set: the image runs `python -m ignosis_eval.app`, which listens on
   `0.0.0.0:$PORT`.
4. In the service **Variables** tab, add `GEMINI_API_KEY` (seal it) and `GEMINI_MODEL`.
5. In the service **Settings → Deploy**, set the **Healthcheck Path** to `/api/health`.
6. **Deploy** (or redeploy after changing variables).
7. In **Settings → Networking**, click **Generate Domain** and open the `*.up.railway.app` URL.

## Check the deployment

- `https://<your-domain>/api/health` returns `{"status":"ok", ..., "live_evaluation": true}`. It returns `false`
  when the key is missing or `GEMINI_MODEL` is an invalid model id.
- The badge at the top right of the app reads **LIVE EVALUATION: `<model>`** when both variables are set, and
  **DEMO / REPLAY only** otherwise.
- **Evaluate a Call → Transcript**: pick a demo call, untick "Replay", then press Evaluate. The result is labelled
  **LIVE EVALUATION** and shows the provider, model and served version in the Evaluator panel.

There is no authentication. The app is for DEV and demo calls only: do not upload real customer calls to a
public deployment.

## Real DEV measurement (local, not on Railway)

This runs against the committed DEV drafts. It is DEV only (no holdout, no red team) and not reliability
evidence.

```bash
pip install -e ".[dev]"
export GEMINI_API_KEY=...            # shell only
export GEMINI_MODEL=...              # optional
ignosis-eval dev smoke               # request, parsing, extraction/judgment schemas, records, failure != PASS
scripts/dev_gemini_baseline.sh       # K0, A, A+, B on the 18 scored DEV calls, then reports/dev-baseline/
```

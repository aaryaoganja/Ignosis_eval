# Review app (Ignosis Call Quality Judge) for Railway or any container host.
# No secret is needed or accepted at build time: this file declares no ARG, so Railway passes no service variable
# into the build. GEMINI_API_KEY is read by the server at runtime from the environment only.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Only what the app needs: the package, the frozen spec pack it loads, and the committed DEV baseline report.
# No benchmark items, drafts, design files, gold, runs or tests enter the image.
COPY pyproject.toml README.md ./
COPY src ./src
COPY docs/spec ./docs/spec
COPY reports/dev-baseline ./reports/dev-baseline

RUN pip install -e ".[web]" \
 && useradd --create-home --uid 10001 appuser \
 && chown -R appuser /app
USER appuser

EXPOSE 8000
# Listens on 0.0.0.0:$PORT (Railway injects PORT; default 8000). Health check: GET /api/health
CMD ["python", "-m", "ignosis_eval.app"]

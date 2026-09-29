"""HTTP server for the review app (FastAPI). The browser talks only to this backend; every Gemini call is made here,
server side, with the key from the runtime environment. No endpoint returns, logs or echoes the key.

    GET  /api/health             liveness (Railway health check)
    GET  /api/config             product text, evaluator configuration (key presence only), modes, profile panel
    GET  /api/demo-calls         synthetic demo calls (DEMO / REPLAY)
    POST /api/evaluate           multipart: mode, transcript | transcript_file, audio_file, demo_id, replay, call_name
                                 (mode=audio is the EXPERIMENTAL Gemini audio path: .wav / .mp3, up to 14 MB)
    GET  /api/calls              call library (in memory; resets on restart)
    GET  /api/calls/{id}         one result
    GET  /api/reliability        DEV engineering measurement (committed report), FINAL VALIDATION: PENDING
    GET  /                       the single-page app (static/)

Run: `python -m ignosis_eval.app` (reads PORT, default 8000) or
`uvicorn ignosis_eval.app.server:app --host 0.0.0.0 --port $PORT`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ignosis_eval.app.service import (
    MAX_AUDIO_BYTES,
    MAX_TRANSCRIPT_BYTES,
    AppError,
    EvaluateRequest,
    ReviewService,
)
from ignosis_eval.spec.loader import load_spec
from ignosis_eval.versions import PACKAGE_VERSION

log = logging.getLogger("ignosis_eval.app")
STATIC = Path(__file__).with_name("static")
MAX_BODY = MAX_AUDIO_BYTES + MAX_TRANSCRIPT_BYTES + 256 * 1024


async def _read_limited(f: UploadFile | None, limit: int, code: str) -> tuple[bytes | None, str | None]:
    if f is None or not f.filename:
        return None, None
    try:
        data = await f.read(limit + 1)
    finally:
        await f.close()  # the spooled temp file is released now; nothing uploaded is kept on disk
    if len(data) > limit:
        size = f"{limit // 2**20} MB" if limit >= 2**20 else f"{limit // 1024} KB"
        raise AppError(413, code, f"The file is larger than the {size} limit.")
    return (data or None), f.filename


def create_app(service: ReviewService | None = None) -> FastAPI:
    svc = service or ReviewService(load_spec())
    app = FastAPI(title="Ignosis Call Quality Judge", version=PACKAGE_VERSION, docs_url=None, redoc_url=None,
                  openapi_url=None)

    @app.middleware("http")
    async def limit_body(request: Request, call_next):  # noqa: ANN001, ANN202
        size = request.headers.get("content-length")
        if size and size.isdigit() and int(size) > MAX_BODY:
            return JSONResponse(AppError(413, "REQUEST_TOO_LARGE", "The upload is too large.").to_json(), 413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else \
            response.headers.get("Cache-Control", "no-cache")
        return response

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(exc.to_json(), status_code=exc.status)

    @app.exception_handler(Exception)
    async def internal_error(_: Request, exc: Exception) -> JSONResponse:  # never leaks details to the browser
        log.error("internal error: %s", type(exc).__name__)
        return JSONResponse({"error": {"code": "INTERNAL", "message": "Internal error. Nothing was evaluated; "
                                                                      "no result is implied.", "pathway": []}}, 500)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": PACKAGE_VERSION, "live_evaluation": svc.live_available()}

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return svc.config()

    @app.get("/api/demo-calls")
    def demo_calls() -> list[dict[str, Any]]:
        return svc.demo_calls()

    @app.post("/api/evaluate")
    async def evaluate(mode: str = Form(...), transcript: str | None = Form(None), demo_id: str | None = Form(None),
                       replay: bool = Form(False), call_name: str | None = Form(None),
                       transcript_file: UploadFile | None = File(None),
                       audio_file: UploadFile | None = File(None)) -> dict[str, Any]:
        tbytes, tname = await _read_limited(transcript_file, MAX_TRANSCRIPT_BYTES, "TRANSCRIPT_TOO_LARGE")
        abytes, aname = await _read_limited(audio_file, MAX_AUDIO_BYTES, "AUDIO_TOO_LARGE")
        req = EvaluateRequest(mode=mode, transcript_text=transcript, transcript_bytes=tbytes,
                              transcript_filename=tname, audio_bytes=abytes, audio_filename=aname,
                              demo_id=demo_id or None, replay=replay, call_name=call_name)
        from starlette.concurrency import run_in_threadpool

        return await run_in_threadpool(svc.evaluate, req)  # provider calls block; keep the event loop free

    @app.get("/api/calls")
    def calls() -> list[dict[str, Any]]:
        return svc.library()

    @app.get("/api/calls/{evaluation_id}")
    def call(evaluation_id: str) -> dict[str, Any]:
        return svc.get(evaluation_id)

    @app.get("/api/reliability")
    def reliability() -> dict[str, Any]:
        return svc.reliability()

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def __getattr__(name: str) -> Any:  # `uvicorn ignosis_eval.app.server:app` builds the app on first access
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)


__all__ = ["create_app"]

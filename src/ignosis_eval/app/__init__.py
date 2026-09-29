"""The review app (clickable MVP): Evaluate a Call, Result, Call Library, Evaluator Reliability.

Primary user: AI Quality Reviewer / Operations QA Reviewer. "Ignosis already listens to every call. This layer
judges whether the AI agent behaved correctly."

- `service.py`: intake, the shared front end, Evaluator B (live Gemini, or DEMO / REPLAY), and the result view.
- `audio_gemini.py`: the EXPERIMENTAL audio-only path (Gemini native audio understanding as an ASR adapter; not
  calibrated, never used by runs, gold or reliability metrics).
- `server.py`: the FastAPI backend and static single-page app.
- `demo_calls.json`: synthetic demo calls with scripted model output.

The app is a host of Evaluator B, like the runner, but it never reads gold, case cards, design intent or run trees
(tests/test_architecture_boundaries.py).
"""

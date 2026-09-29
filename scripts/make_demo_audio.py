"""Generate the review app's sample call recording from a demo call's transcript (synthetic speech only).

    python scripts/make_demo_audio.py [demo-id]        # default: demo-settlement

- **Voices.** The recording uses espeak-ng, an offline rule-based formant synthesizer. Its voices are fully
  synthetic and not modelled on any real person. It needs `espeak-ng` on PATH at generation time only; the app
  does not need it.
- **Content.** Every line is the demo call's own fictional transcript, spoken turn by turn (agent: female
  variant; borrower: male variant), so the audio and the transcript correspond one to one.
- **Format.** 8 kHz 16-bit mono PCM WAV (telephone quality), with a short pause between turns.
- **Output.** Written to `src/ignosis_eval/app/static/demo/<demo-id>.wav`. The file's sha256, duration and
  generator are recorded in `src/ignosis_eval/app/demo_calls.json` under the call's `audio` key.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tempfile
import warnings
import wave
from pathlib import Path

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    import audioop  # stdlib until 3.13; generation-time only

REPO = Path(__file__).resolve().parents[1]
DEMO_FILE = REPO / "src" / "ignosis_eval" / "app" / "demo_calls.json"
OUT_DIR = REPO / "src" / "ignosis_eval" / "app" / "static" / "demo"
VOICES = {"AGENT": ("en-us+female3", 150, 55), "BORROWER": ("en-gb+male4", 145, 45)}  # voice, words/min, pitch
RATE, PAUSE_S = 8000, 0.45


def speak(text: str, voice: str, speed: int, pitch: int) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "turn.wav"
        subprocess.run(["espeak-ng", "-v", voice, "-s", str(speed), "-p", str(pitch), "-w", str(out), text],
                       check=True)
        with wave.open(str(out), "rb") as w:
            frames, width, rate = w.readframes(w.getnframes()), w.getsampwidth(), w.getframerate()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        converted, _ = audioop.ratecv(frames, width, 1, rate, RATE, None)
        return audioop.lin2lin(converted, width, 2) if width != 2 else converted


def main(demo_id: str = "demo-settlement") -> int:
    doc = json.loads(DEMO_FILE.read_text(encoding="utf-8"))
    call = next(c for c in doc["calls"] if c["id"] == demo_id)
    pause = b"\x00\x00" * int(RATE * PAUSE_S)
    pcm = bytearray(pause)
    for line in call["transcript"].strip().splitlines():
        role, _, text = line.partition(":")
        voice, speed, pitch = VOICES[role.strip()]
        pcm += speak(text.strip(), voice, speed, pitch) + pause
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(pcm))
    data = buf.getvalue()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{demo_id}.wav"
    out.write_bytes(data)
    call["audio"] = {"file": f"demo/{out.name}", "format": "wav", "sha256": hashlib.sha256(data).hexdigest(),
                     "duration_s": round(len(pcm) / 2 / RATE, 1),
                     "generator": "espeak-ng (synthetic formant voices; no real person's voice), 8 kHz mono",
                     "fictional": True}
    DEMO_FILE.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(REPO)} ({len(data) // 1024} KB, {call['audio']['duration_s']} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))

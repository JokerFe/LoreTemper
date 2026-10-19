import time, json, fcntl
from pathlib import Path
from video_tutor.asr import transcribe_window
from video_tutor.config import Settings

root = Path(__file__).resolve().parents[1]
model = root / "work" / "ggml-large-v3-turbo.bin"
for _ in range(1800):
    if model.is_file():
        break
    time.sleep(2)
else:
    raise RuntimeError("Whisper model still downloading")
lock = (root / "work" / "model-test.lock").open("w")
fcntl.flock(lock, fcntl.LOCK_EX)
s = Settings(language="en", whisper_model_path=str(model))
started = time.monotonic()
segments = transcribe_window(
    Path("/opt/homebrew/share/whisper-cpp/jfk.wav"), root / "work" / "asr-smoke", s, 0, 60000, lambda: None
)
text = " ".join(s["raw_text"] for s in segments)
assert "country" in text.lower() and "americans" in text.lower(), text
report = {
    "source": "whisper.cpp bundled JFK speech sample",
    "backend": "whisper.cpp large-v3-turbo",
    "seconds": time.monotonic() - started,
    "transcript": text,
    "segments": len(segments),
}
(root / "work" / "real-asr-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)

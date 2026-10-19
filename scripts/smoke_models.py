import fcntl

"""Real local-model smoke test on a clearly identified synthetic recording."""

import os, time, json, resource
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.environ["VIDEO_TUTOR_DATA"] = str(root / "work" / "model-smoke-data")
from video_tutor import db
from video_tutor.config import Settings, save_settings, data_dir
from video_tutor.worker import import_file, claim
from video_tutor.pipeline import Pipeline

test_lock = (root / "work" / "model-test.lock").open("w")
fcntl.flock(test_lock, fcntl.LOCK_EX)
db.init_db()
s = Settings(directories=[str(root / "work" / "demo")], require_ac_power=False, min_free_disk_gb=3)
save_settings(s)
aid = import_file(root / "work" / "demo" / "synthetic-tutorial.mp4", s, stable=False)
run = claim("real-smoke")
if not run:
    db.create_run(aid, s)
    run = claim("real-smoke")
started = time.monotonic()
try:
    doc = Pipeline(run, "real-smoke").execute()
    report = {
        "source": "synthetic fixture, not user content",
        "run_id": run["id"],
        "seconds": time.monotonic() - started,
        "frames": len(doc["frames"]),
        "steps": sum(len(c["steps"]) for c in doc["chapters"]),
        "peak_python_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "overview": doc["overview"],
        "outputs": str(data_dir() / "assets" / aid / "runs" / run["id"] / "exports"),
    }
    (root / "work" / "real-model-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
except Exception as e:
    db.execute("UPDATE runs SET status='failed',error=? WHERE id=?", (str(e), run["id"]))
    raise

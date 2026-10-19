"""Exercise directory discovery, the real worker/models, evidence and exports in a fresh library."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.is_file():
        parser.error("source must be an existing video file")
    root = Path(__file__).resolve().parents[1]
    folder = Path(tempfile.mkdtemp(prefix="auto-validation-", dir=root / "work"))
    videos = folder / "videos"
    videos.mkdir()
    os.environ["VIDEO_TUTOR_DATA"] = str(folder / "data")
    from video_tutor import db
    from video_tutor.config import Settings, save_settings
    from video_tutor.pipeline import validate_refs
    from video_tutor.retrieval import search

    db.init_db()
    save_settings(
        Settings(
            directories=[str(videos)], stable_seconds=1, scan_seconds=5, require_ac_power=False, min_free_disk_gb=3
        )
    )
    started = time.monotonic()
    print(json.dumps({"validation_dir": str(folder), "source": str(source)}, ensure_ascii=False), flush=True)
    with (folder / "worker.log").open("w") as log:
        worker = subprocess.Popen([sys.executable, "-m", "video_tutor", "worker"], stdout=log, stderr=log)
        report = {"source": str(source), "fresh_database": True}
        try:
            shutil.copy2(source, videos / source.name)
            previous = None
            while time.monotonic() - started < args.timeout:
                if worker.poll() is not None:
                    raise RuntimeError("Worker exited; see worker.log")
                run = db.one("SELECT * FROM runs ORDER BY created_at DESC LIMIT 1")
                if run:
                    state = (run["status"], run["stage"])
                    if state != previous:
                        print(
                            json.dumps(
                                {
                                    "status": state[0],
                                    "stage": state[1],
                                    "seconds": round(time.monotonic() - started, 1),
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                        previous = state
                    if run["status"] in ("succeeded", "succeeded_with_warnings"):
                        break
                    if run["status"] in ("failed", "cancelled", "paused"):
                        raise RuntimeError(run["error"] or run["status"])
                time.sleep(1)
            else:
                raise TimeoutError("Automatic pipeline did not finish within the timeout")
            doc = db.latest_document(run["id"])["body"]
            assert doc["chapters"] and doc["overview"]["summary"]
            assert doc["frames"]
            ids = set(doc["frames"]) | {t["id"] for t in doc["transcript"]}
            validate_refs(doc, ids)
            for frame in doc["frames"].values():
                artifact = db.one("SELECT * FROM artifacts WHERE id=?", (frame["artifact_id"],))
                assert artifact and (folder / "data" / artifact["path"]).is_file()
                assert db.file_sha(folder / "data" / artifact["path"]) == artifact["sha256"]
            run_dir = folder / "data/assets" / run["asset_id"] / "runs" / run["id"]
            archives = list((run_dir / "exports").rglob("*.zip"))
            assert archives
            for archive in archives:
                with zipfile.ZipFile(archive) as z:
                    assert z.testzip() is None
                    names = z.namelist()
                    for extension in (".md", ".html", ".json", ".srt", ".jpg"):
                        assert any(n.endswith(extension) for n in names), (extension, names)
            assert search(doc["overview"]["title"])
            if json.loads(db.one("SELECT media_json FROM assets WHERE id=?", (run["asset_id"],))["media_json"])[
                "audio"
            ]:
                assert doc["transcript"], "Audio fixture produced no transcript"
                assert any(p.stat().st_size for p in (run_dir / "exports").rglob("*.srt"))
            report.update(
                status=run["status"],
                run_id=run["id"],
                seconds=round(time.monotonic() - started, 1),
                overview=doc["overview"],
                transcript=doc["transcript"],
                frames=len(doc["frames"]),
                steps=sum(len(c["steps"]) for c in doc["chapters"]),
                warnings=doc["warnings"],
                exports=str(run_dir / "exports"),
                metrics=json.loads((run_dir / "metrics.json").read_text()),
            )
        except KeyboardInterrupt:
            report.update(status="interrupted", seconds=round(time.monotonic() - started, 1))
            raise
        except Exception as exc:
            report.update(status="failed", error=str(exc), seconds=round(time.monotonic() - started, 1))
            raise
        finally:
            (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

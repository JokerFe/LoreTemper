import time
import subprocess
from fastapi.testclient import TestClient
from video_tutor import db
from video_tutor.api import app
from video_tutor.config import Settings, save_settings, local_token
from video_tutor.media import binary, probe, select_frames
from video_tutor.worker import claim
from video_tutor.pipeline import Pipeline, StopWork
import pytest


def test_cancel_does_not_allow_old_worker_commit(asset):
    rid = db.create_run(asset, Settings(min_free_disk_gb=0))
    run = claim("owner")
    p = Pipeline(run, "owner")
    db.control(rid, "cancel")
    with pytest.raises(StopWork):
        p.unit("x", "one", {}, lambda: {"bad": True})
    assert not db.rows("SELECT * FROM units")
    assert db.one("SELECT status FROM runs WHERE id=?", (rid,))["status"] == "cancelled"


def test_expired_lease_recovery(asset):
    rid = db.create_run(asset, Settings())
    r = claim("old")
    db.execute("UPDATE runs SET lease_expires=? WHERE id=?", (time.time() - 1, rid))
    n = claim("new")
    assert n["id"] == rid and n["generation"] == r["generation"] + 1
    with pytest.raises(StopWork):
        Pipeline(r, "old").check()


def test_import_is_queued_and_paths_are_restricted(tmp_path):
    videos = tmp_path / "videos"
    videos.mkdir()
    save_settings(Settings(directories=[str(videos)]))
    with TestClient(app) as c:
        c.post("/api/v1/session", json={"token": local_token()})
        r = c.post("/api/v1/imports", json={"path": str(videos)}, headers={"x-vt-request": "1"})
        assert r.status_code == 202
        assert db.one("SELECT status FROM imports")["status"] == "queued"
        assert c.post("/api/v1/imports", json={"path": str(tmp_path)}, headers={"x-vt-request": "1"}).status_code == 400


def test_vfr_frames_keep_actual_presentation_times(tmp_path):
    file = tmp_path / "vfr.mp4"
    subprocess.run(
        [
            binary("ffmpeg"),
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=10:duration=4",
            "-vf",
            "select='not(mod(n,3))+not(mod(n,7))'",
            "-fps_mode",
            "vfr",
            "-c:v",
            "libx264",
            str(file),
        ],
        check=True,
    )
    meta = probe(file)
    picked = select_frames(file, tmp_path / "frames", meta, Settings(candidate_fps=4), lambda: None, lambda a, b: None)
    assert len(picked["frames"]) > 2
    import av

    with av.open(str(file)) as container:
        real = {
            round(float(f.pts * f.time_base) * 1000 - meta["origin_seconds"] * 1000) for f in container.decode(video=0)
        }
    assert all(f["time_ms"] in real for f in picked["frames"])

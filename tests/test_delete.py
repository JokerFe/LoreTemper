from fastapi.testclient import TestClient
from video_tutor import db
from video_tutor.api import app
from video_tutor.config import Settings, save_settings, local_token
from video_tutor.worker import import_file
from test_media import create_fixture


def test_delete_keeps_source_and_does_not_auto_reimport(tmp_path):
    source = create_fixture(tmp_path / "videos")
    settings = Settings(directories=[str(source.parent)], stable_seconds=1)
    save_settings(settings)
    aid = import_file(source, settings, stable=False)
    db.execute("UPDATE runs SET status='cancelled' WHERE asset_id=?", (aid,))
    with TestClient(app) as c:
        c.post("/api/v1/session", json={"token": local_token()})
        r = c.delete("/api/v1/assets/" + aid, headers={"x-vt-request": "1"})
        assert r.status_code == 200
    assert source.exists()
    assert import_file(source, settings, stable=False) is None
    assert not db.rows("SELECT * FROM assets")

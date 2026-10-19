import pytest
from video_tutor import db


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_TUTOR_DATA", str(tmp_path / "data"))
    db.init_db()


@pytest.fixture
def asset(tmp_path):
    import time

    db.execute("INSERT INTO assets VALUES (?,?,?,?,?,?)", ("v_test", "a" * 64, "测试教程", 10, None, time.time()))
    return "v_test"

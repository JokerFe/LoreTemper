import pytest
from fastapi.testclient import TestClient
from video_tutor import db
from video_tutor.api import app
from video_tutor.config import Settings, data_dir, local_token, save_settings
from video_tutor.worker import claim, is_allowed
from video_tutor.pipeline import Pipeline, StopWork, validate_refs, frame_groups
from video_tutor.asr import merge_segments
from video_tutor.retrieval import index_document, search
from video_tutor.exporting import export_document, srt


def test_queue_recovery_and_pause(asset):
    r = db.create_run(asset, Settings())
    run = claim("w1")
    assert run["id"] == r and run["generation"] == 1
    assert claim("w2") is None
    db.control(r, "pause")
    with pytest.raises(StopWork):
        Pipeline(run, "w1").check()
    assert db.one("SELECT status FROM runs WHERE id=?", (r,))["status"] == "paused"
    db.control(r, "resume")
    again = claim("w1")
    assert again["generation"] == 2
    with pytest.raises(StopWork):
        Pipeline(run, "w1").check()


def test_checkpoint_survives_restart_and_detects_corruption(asset):
    db.create_run(asset, Settings())
    run = claim("w")
    p = Pipeline(run, "w")
    calls = []

    def compute():
        calls.append(1)
        return {"value": 42}

    assert p.unit("fuse", "one", {}, compute) == {"value": 42}
    assert p.unit("fuse", "one", {}, compute) == {"value": 42}
    assert len(calls) == 1
    unit = db.one("SELECT * FROM units")
    (data_dir() / unit["artifact_path"]).write_text("{}")
    p.unit("fuse", "one", {}, compute)
    assert len(calls) == 2
    p.unit("fuse", "one", {"changed": True}, compute)
    assert len(calls) == 3


def test_refs_and_frame_windows():
    with pytest.raises(ValueError):
        validate_refs({"evidence_ids": ["fake"]}, {"f1"})
    frames = [{"id": str(i), "time_ms": i * 8000} for i in range(5)]
    assert [len(x) for x in frame_groups(frames, 4)] == [2, 2, 1]


def test_overlap_preserves_later_repetition():
    a = {"id": "t1", "start_ms": 1000, "end_ms": 3000, "raw_text": "点击保存"}
    b = {**a, "id": "t2", "start_ms": 1100}
    c = {**a, "id": "t3", "start_ms": 20000, "end_ms": 23000}
    assert len(merge_segments([a], [b, c])) == 2
    assert "00:00:01,000 --> 00:00:03,000" in srt([a])


def test_paths_and_cloud_rejected(tmp_path):
    root = tmp_path / "videos"
    root.mkdir()
    outside = tmp_path / "private"
    outside.mkdir()
    (root / "alias").symlink_to(outside, target_is_directory=True)
    s = Settings(directories=[str(root)])
    assert is_allowed(root / "a.mp4", s)
    assert not is_allowed(root / "alias" / "a.mp4", s)
    with pytest.raises(ValueError):
        Settings(ollama_url="https://example.com")
    with pytest.raises(ValueError):
        Settings(vision_model="qwen:cloud")


def sample_doc(asset, rid):
    return {
        "schema_version": 1,
        "asset_id": asset,
        "run_id": rid,
        "source_title": "测试",
        "duration_ms": 10000,
        "overview": {
            "title": "端口设置",
            "summary": "修改端口",
            "summary_evidence_ids": ["t1"],
            "goals": [],
            "prerequisites": [],
            "key_points": [],
            "verification": [],
            "unresolved_items": [],
        },
        "chapters": [
            {
                "title": "设置",
                "summary": "<script>alert(1)</script>",
                "steps": [
                    {
                        "title": "修改端口",
                        "action": "输入8080",
                        "start_ms": 1000,
                        "end_ms": 3000,
                        "evidence_ids": ["t1"],
                        "evidence_type": "speech",
                        "parameters": [],
                        "uncertainties": [],
                    }
                ],
                "unresolved_items": [],
            }
        ],
        "transcript": [{"id": "t1", "start_ms": 1000, "end_ms": 3000, "raw_text": "端口设置为8080"}],
        "frames": {},
        "observations": [],
        "warnings": [],
        "provenance": {},
        "user_notes": "",
    }


def test_chinese_index_and_export(asset):
    rid = db.create_run(asset, Settings())
    doc = sample_doc(asset, rid)
    index_document(doc)
    assert search("端口")
    assert search("8080")
    index_document(doc)
    assert len(db.rows("SELECT * FROM search_chunks")) == 3
    output = data_dir() / "export"
    export_document(rid, doc, output)
    html = (output / "document.html").read_text()
    assert "<script>" not in html
    assert (output / "video-notes-v1.zip").exists()
    v = db.save_document(rid, doc)
    assert v == 1
    with pytest.raises(ValueError):
        db.save_document(rid, doc, "user", 0)


def test_api_auth_origin_and_range(tmp_path, asset):
    settings = Settings(directories=[str(tmp_path)], require_ac_power=False)
    save_settings(settings)
    with TestClient(app) as c:
        assert c.get("/api/v1/assets").status_code == 401
        assert c.post("/api/v1/session", json={"token": local_token()}).status_code == 200
        assert c.get("/api/v1/assets").status_code == 200
        assert c.put("/api/v1/settings", json=settings.model_dump()).status_code == 403
        assert (
            c.put(
                "/api/v1/settings",
                headers={"x-vt-request": "1", "origin": "https://evil.example"},
                json=settings.model_dump(),
            ).status_code
            == 403
        )
        assert c.put("/api/v1/settings", headers={"x-vt-request": "1"}, json=settings.model_dump()).status_code == 200
        p = tmp_path / "test.mp4"
        p.write_bytes(b"0123456789")
        db.execute("INSERT INTO sources VALUES (?,?,?)", (str(p), asset, "{}"))
        r = c.get("/media/assets/" + asset, headers={"Range": "bytes=2-5"})
        assert r.status_code == 206 and r.content == b"2345"
        assert c.get("/media/assets/" + asset, headers={"Range": "bytes=99-100"}).status_code == 416
        r = c.get("/media/assets/" + asset, headers={"Range": "bytes=-2"})
        assert r.content == b"89"


def test_model_schema_constrains_reference_ids_and_times():
    from video_tutor.llm import constrained_schema, collect_evidence
    from video_tutor.schemas import Chapter

    schema = constrained_schema(Chapter.model_json_schema(), ["f_001", "t_001"], (3000, 6000))
    props = schema["$defs"]["Step"]["properties"]
    assert props["evidence_ids"]["items"]["enum"] == ["f_001", "t_001"]
    assert props["start_ms"]["minimum"] == 3000 and props["end_ms"]["maximum"] == 6000
    assert collect_evidence({"id": "o_0", "frame_ids": ["f_001"], "transcript": [{"id": "t_001"}]}) == {
        "f_001",
        "t_001",
    }


def test_playback_anchors_follow_evidence_not_model_time():
    from video_tutor.pipeline import anchor_step_times

    steps = [
        {"start_ms": 0, "end_ms": 0, "evidence_ids": ["f6"]},
        {"start_ms": 0, "end_ms": 0, "evidence_ids": ["f3", "t1"]},
    ]
    anchor_step_times(steps, {"f6": (6000, 6000), "f3": (3000, 3000), "t1": (2000, 4000)}, 2500, 10000)
    assert [(s["start_ms"], s["end_ms"]) for s in steps] == [(2500, 4000), (6000, 6000)]
    with pytest.raises(ValueError):
        anchor_step_times([{"evidence_ids": ["f6"]}], {"f6": (6000, 6000)}, 7000, 10000)

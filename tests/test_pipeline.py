import pytest
from video_tutor import db
from video_tutor.config import Settings, save_settings, data_dir
from video_tutor.worker import import_file, claim
from video_tutor.pipeline import Pipeline
from video_tutor.schemas import Observation, Chapter, Overview
from test_media import create_fixture


@pytest.mark.parametrize("visual_analysis", ["full", "keyframes"])
def test_full_pipeline_with_explicit_fake_model(tmp_path, monkeypatch, visual_analysis):
    source = create_fixture(tmp_path / "videos")
    s = Settings(directories=[str(source.parent)], require_ac_power=False, min_free_disk_gb=0)
    save_settings(s)
    aid = import_file(source, s, stable=False)
    options = {"visual_analysis": visual_analysis}
    db.execute("UPDATE runs SET options_json=? WHERE asset_id=?", (db.dumps(options), aid))
    run = claim("test-worker")
    monkeypatch.setattr(
        "video_tutor.pipeline.model_inventory",
        lambda _: [{"name": s.vision_model, "digest": "fake-vision"}, {"name": s.text_model, "digest": "fake-text"}],
    )
    calls = []

    class FakeLLM:
        def __init__(self, *args):
            pass

        def unload(self, *args):
            pass

        def chat(self, model, schema, system, payload, frames=None):
            calls.append(schema.__name__)
            if schema == Observation:
                return Observation(
                    description="测试夹具展示端口值变化", frame_ids=[f["id"] for f in frames], needs_crop=False
                ).model_dump()
            if schema == Chapter:
                refs = payload["observations"][0]["frame_ids"]
                return Chapter(
                    title="测试设置",
                    summary="用于验证程序流程的模拟结果",
                    steps=[
                        {
                            "title": "查看设置",
                            "action": "测试画面",
                            "start_ms": payload["start_ms"],
                            "end_ms": payload["end_ms"],
                            "evidence_ids": refs,
                            "evidence_type": "visual",
                        }
                    ],
                ).model_dump()
            refs = payload["chapters"][0]["evidence_ids"]
            return Overview(title="测试教程", summary="明确的模拟模型测试", summary_evidence_ids=refs).model_dump()

    monkeypatch.setattr("video_tutor.pipeline.LocalLLM", FakeLLM)
    doc = Pipeline(run, "test-worker").execute()
    assert doc["asset_id"] == aid
    assert doc["coverage"]["visual_analysis"]["mode"] == visual_analysis
    assert doc["provenance"]["run_options"] == options
    assert db.one("SELECT status FROM runs WHERE id=?", (run["id"],))["status"] == "succeeded_with_warnings"
    assert db.latest_document(run["id"])["body"]["frames"]
    assert db.rows("SELECT * FROM artifacts WHERE kind='export'")
    assert {"Observation", "Chapter", "Overview"} <= set(calls)
    assert list((data_dir() / "assets" / aid / "runs" / run["id"] / "exports").rglob("*.zip"))
    # A new run with matching inputs reuses valid analysis units, including all cited frame files.
    db.create_run(aid, s, options)
    second = claim("test-worker")
    calls.clear()
    doc2 = Pipeline(second, "test-worker").execute()
    assert calls == []
    assert all(db.one("SELECT id FROM artifacts WHERE id=?", (f["artifact_id"],)) for f in doc2["frames"].values())


def test_generation_policy_invalidates_model_cache_only(asset, monkeypatch):
    db.create_run(asset, Settings(min_free_disk_gb=0))
    first = Pipeline(claim("test-worker"), "test-worker")
    first.unit("transcribe", "one", "same-input", lambda: {"text": "cached transcript"})
    first.unit("analyze_frames", "one", "same-input", lambda: {"description": "old policy"})

    monkeypatch.setattr("video_tutor.pipeline.GENERATION_POLICY_VERSION", 999)
    db.create_run(asset, Settings(min_free_disk_gb=0))
    second = Pipeline(claim("test-worker"), "test-worker")
    calls = []

    def recompute():
        calls.append(True)
        return {"description": "new policy"}

    assert second.unit("transcribe", "one", "same-input", recompute) == {"text": "cached transcript"}
    assert not calls
    assert second.unit("analyze_frames", "one", "same-input", recompute) == {"description": "new policy"}
    assert calls == [True]


def test_fusion_compaction_preserves_values_uncertainty_and_each_source():
    import copy
    from video_tutor.pipeline import compact_observations, validate_refs

    original = [
        {
            "id": "o_1",
            "description": "参数改变",
            "frame_ids": ["f_0", "f_1", "f_2"],
            "visible_texts": [
                {"text": "24 × 24", "frame_id": "f_0", "uncertainty": ""},
                {"text": "24 × 24", "frame_id": "f_1", "uncertainty": ""},
                {"text": "32 × 32", "frame_id": "f_2", "uncertainty": ""},
                {"text": "24 × 24", "frame_id": "f_2", "uncertainty": "模糊"},
                {"text": None, "frame_id": "f_1", "uncertainty": "不可读"},
                {"text": None, "frame_id": "f_2", "uncertainty": "不可读"},
            ],
        }
    ]
    before = copy.deepcopy(original)
    compact = compact_observations(original)
    assert original == before

    def evidence(value):
        return {
            (t["text"], t["uncertainty"], fid)
            for o in value
            for t in o["visible_texts"]
            for fid in (t["frame_ids"] if "frame_ids" in t else [t["frame_id"]])
        }

    assert evidence(compact) == evidence(original)
    assert len(compact[0]["visible_texts"]) == 4
    assert compact[0]["description"] == original[0]["description"]
    validate_refs(compact, {"f_0", "f_1", "f_2"})

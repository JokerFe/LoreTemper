import json
import time
from pathlib import Path
import pytest
from video_tutor import db
from video_tutor.config import data_dir
from video_tutor.workflows import (
    WorkflowInput,
    StageConfig,
    resolve,
    resolve_stage,
    submit,
    tick,
    jobs,
    control,
    generate_skill,
    WorkflowStopped,
)


def catalog(text_only=False):
    return {
        "logged_in": True,
        "models": [
            {
                "model": "current-model",
                "inputModalities": ["text"] if text_only else ["text", "image"],
                "isDefault": True,
                "supportedReasoningEfforts": [{"reasoningEffort": "medium"}],
                "serviceTiers": [],
            }
        ],
    }


def sources(tmp_path):
    video = tmp_path / "lesson.mp4"
    video.write_bytes(b"video")
    note = tmp_path / "notes.md"
    note.write_text("# 图标\n\n通过轮廓、负形与视觉重量判断一致性。\n来源 12:30。")
    return video, note


def setup(monkeypatch, video, note, tmp_path):
    monkeypatch.setattr("video_tutor.workflows.provider_models", lambda _: catalog())
    monkeypatch.setattr(
        "video_tutor.workflows.default_inputs",
        lambda: {"video_paths": [str(video)], "note_paths": [str(note)], "output_directory": str(tmp_path / "out")},
    )
    monkeypatch.setattr("video_tutor.analysis_control.probe", lambda _: {"duration_ms": 60000})
    monkeypatch.setattr("video_tutor.workflows.ensure_workflow_worker", lambda: None)


def test_independent_modes_and_auto_configuration(tmp_path, monkeypatch):
    video, note = sources(tmp_path)
    setup(monkeypatch, video, note, tmp_path)
    study = resolve(WorkflowInput())
    assert study["video_paths"] == [str(video)] and study["skill"] is None
    skill = resolve(WorkflowInput(mode="skill"))
    assert skill["note_paths"] == [str(note)] and skill["study"] is None
    combined = resolve(WorkflowInput(mode="combined"))
    assert combined["study"]["model"] == "current-model" and combined["skill"]["model"] == "current-model"
    assert combined["notes"] == []  # no silent inclusion of unrelated prior courses
    assert combined["estimates"]["skill"]["api_equivalent_usd"] is None
    assert skill["notes"][0]["sha256"] == db.file_sha(note)
    assert Path(skill["output_directory"]) == tmp_path / "out"


def test_skill_text_model_does_not_require_vision(tmp_path, monkeypatch):
    monkeypatch.setattr("video_tutor.workflows.provider_models", lambda _: catalog(True))
    assert resolve_stage(StageConfig(provider="codex"), False, {})["model"] == "current-model"
    with pytest.raises(ValueError, match="兼容模型"):
        resolve_stage(StageConfig(provider="codex"), True, {})


def test_auto_provider_fallback_is_preview_only(monkeypatch):
    def inventory(provider):
        if provider == "codex":
            raise RuntimeError("not signed in")
        return {"logged_in": True, "models": [{"model": "local-text", "inputModalities": ["text"]}]}

    monkeypatch.setattr("video_tutor.workflows.provider_models", inventory)
    resolved = resolve_stage(StageConfig(), False, {})
    assert resolved["provider"] == "ollama" and resolved["model"] == "local-text"
    with pytest.raises(ValueError, match="not signed in"):
        resolve_stage(StageConfig(provider="codex"), False, {})


def test_archives_and_non_notes_excluded(tmp_path, monkeypatch):
    from video_tutor.workflows import note_files

    _, note = sources(tmp_path)
    (tmp_path / "review.md").write_text("not a main note")
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "notes.md").write_text("withdrawn")
    assert [n["path"] for n in note_files([str(tmp_path)])] == [str(note)]
    assert note_files([str(archive / "notes.md")]) == []


def test_workflow_job_identity_and_pause_before_import(tmp_path, monkeypatch):
    video, note = sources(tmp_path)
    setup(monkeypatch, video, note, tmp_path)
    body = WorkflowInput(mode="combined", request_id="wf_test")
    submit(body)
    assert submit(body)["reused"]
    queued = jobs()[0]
    assert queued["status"] == "queued"
    control("wf_test", "pause")
    with pytest.raises(WorkflowStopped):
        tick(db.one("SELECT * FROM workflows WHERE id=?", ("wf_test",)))
    control("wf_test", "resume")
    assert jobs()[0]["status"] == "queued"
    config = json.loads(db.one("SELECT config_json FROM workflows")["config_json"])
    assert config["study"]["provider"] == "codex" and config["video_paths"] == [str(video)]


def test_chain_waits_for_all_studies_and_freezes_handoff(tmp_path, monkeypatch):
    video, note = sources(tmp_path)
    setup(monkeypatch, video, note, tmp_path)
    submit(WorkflowInput(mode="combined", request_id="wf_chain"))
    monkeypatch.setattr("video_tutor.workflows.enqueue", lambda body: {"batch_id": body.request_id})
    def job():
        return db.one("SELECT * FROM workflows WHERE id=?", ("wf_chain",))
    tick(job())
    assert job()["status"] == "learning"
    tick(job())
    assert job()["status"] == "learning"
    db.execute("INSERT INTO study_batches VALUES (?,?,?)", ("study-wf_chain", "{}", time.time()))
    db.execute("INSERT INTO assets VALUES (?,?,?,?,?,?)", ("v_chain", "hash", "course", 1, "{}", time.time()))
    from video_tutor.config import Settings

    run = db.create_run("v_chain", Settings())
    db.execute(
        "INSERT INTO study_batch_items VALUES (?,?,?,?,?,?)",
        ("i_chain", "study-wf_chain", str(video), "imported", run, None),
    )
    tick(job())
    assert job()["status"] == "learning"
    db.execute("UPDATE runs SET status='succeeded_with_warnings' WHERE id=?", (run,))
    document = {
        "overview": {"title": "course", "summary": "核心思想"},
        "source_title": "course",
        "duration_ms": 60000,
        "summary_markdown": "# 学习总结\n判断方法与条件。",
        "warnings": ["待审阅"],
        "user_notes": "",
    }
    db.save_document(run, document)
    calls = []

    def generate(job, config, notes):
        calls.append(notes)
        assert len(notes) == 1 and "判断方法与条件" in Path(notes[0]["path"]).read_text()
        return {"skill_file": "/output/SKILL.md"}

    monkeypatch.setattr("video_tutor.workflows.generate_skill", generate)
    tick(job())
    assert job()["status"] == "succeeded" and len(calls) == 1
    assert (data_dir() / "workflows/wf_chain/handoff.json").exists()


def test_skill_package_structure_provenance_and_source_changes(tmp_path, monkeypatch):
    video, note = sources(tmp_path)
    setup(monkeypatch, video, note, tmp_path)
    submit(WorkflowInput(mode="skill", request_id="wf_skill"))

    class Runner:
        def __init__(self, folder, config, ident):
            self.output = folder

        def call(self, label, prompt):
            if label == "skill-entry":
                return "---\nname: course-design-guide\ndescription: 从课程图标笔记指导图标一致性检查。\n---\n\n# 图标一致性\n\n先分析轮廓、负形与视觉重量，保留课程语境。来源未全部复核，待实际任务验证。\n\n深入判断见[来源规则](references/principles.md)，证据见[来源索引](references/sources.md)。\n"
            return "S001：基于图标课程12:30的轮廓、负形和视觉重量方法；适用于同套图标的光学一致性判断。\n" * 8

    monkeypatch.setattr("video_tutor.workflows.TextRunner", Runner)
    job = db.one("SELECT * FROM workflows WHERE id=?", ("wf_skill",))
    config = json.loads(job["config_json"])
    result = generate_skill(job, config, config["notes"])
    package = Path(result["skill_file"]).parent
    assert package.joinpath("references/principles.md").exists()
    assert package.joinpath("references/sources.md").exists()
    assert result["installed"] is False and result["behavioral_validation"] == "not_run"
    assert result["status"] == "generated_awaiting_validation"
    note.write_text("changed source")
    with pytest.raises(ValueError, match="已变化"):
        generate_skill(job, config, config["notes"])


def test_request_identity_cannot_escape_output_root():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WorkflowInput(request_id="../../outside")


def test_workflow_http_routes_and_generation_do_not_start_on_preview(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from video_tutor.api import app
    from video_tutor.config import local_token

    video, note = sources(tmp_path)
    setup(monkeypatch, video, note, tmp_path)
    client = TestClient(app)
    client.cookies.set("vt_session", local_token())
    client.headers["X-VT-Request"] = "1"
    assert client.get("/api/v1/workflows/defaults").status_code == 200
    response = client.post("/api/v1/workflows/resolve", json={"mode": "skill"})
    assert response.status_code == 200 and response.json()["study"] is None
    assert client.get("/api/v1/workflows/jobs").json() == []
    response = client.post("/api/v1/workflows/jobs", json={"mode": "skill", "request_id": "api_job"})
    assert response.status_code == 202
    assert client.get("/api/v1/workflows/jobs").json()[0]["status"] == "queued"
    assert client.post("/api/v1/workflows/jobs/api_job/pause", json={}).json()["status"] == "paused"
    assert client.post("/api/v1/workflows/jobs/api_job/resume", json={}).json()["status"] == "queued"

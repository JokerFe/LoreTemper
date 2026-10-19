import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from video_tutor import db
from video_tutor.analysis_control import (
    StudyInput,
    enqueue,
    preview,
    process_study_batches,
    selected_videos,
    validate_provider,
)
from video_tutor.api import app
from video_tutor.config import Settings, load_settings, local_token, save_settings
from video_tutor.estimates import BASE_USAGE, cost_multiplier, equivalent_cost
from video_tutor.worker import claim, is_allowed


def catalog():
    return {
        "logged_in": True,
        "source": "test",
        "models": [
            {
                "model": "new-runtime-model",
                "inputModalities": ["text", "image"],
                "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
                "serviceTiers": [],
            }
        ],
    }


def client():
    c = TestClient(app)
    c.cookies.set("vt_session", local_token())
    c.headers["X-VT-Request"] = "1"
    return c


def test_preview_deduplicates_and_reports_broken_files(tmp_path, monkeypatch):
    directory = tmp_path / "videos"
    directory.mkdir()
    good = directory / "课.mp4"
    good.write_bytes(b"video")
    bad = directory / "bad.mov"
    bad.write_bytes(b"bad")
    (directory / "unrelated.pdf").write_bytes(b"pdf")
    output = directory / "exports"
    output.mkdir()
    (output / "copy.mp4").write_bytes(b"not input")

    def probe(path):
        if path == bad:
            raise ValueError("broken")
        return {"duration_ms": 3600000}

    monkeypatch.setattr("video_tutor.analysis_control.probe", probe)
    result = preview(StudyInput(paths=[str(directory), str(good)], output_directory=str(output)))
    assert len(result["files"]) == 1 and len(result["errors"]) == 1
    assert result["duration_ms"] == 3600000
    assert result["estimate"]["total_tokens"] == 632243
    assert 1.13 < result["estimate"]["api_equivalent_usd"] < 1.15
    assert selected_videos([str(directory), str(good)], output) == [bad, good]


def test_runtime_catalog_controls_model_effort_and_speed(monkeypatch, tmp_path):
    monkeypatch.setattr("video_tutor.analysis_control.provider_models", lambda _: catalog())
    body = StudyInput(paths=["x"], output_directory=str(tmp_path), model="new-runtime-model", reasoning="high")
    validate_provider(body)
    for update in ({"model": "not-in-catalog"}, {"reasoning": "medium"}, {"service_tier": "priority"}):
        with pytest.raises(ValueError):
            validate_provider(body.model_copy(update=update))
    monkeypatch.setattr("video_tutor.analysis_control.provider_models", lambda _: {**catalog(), "logged_in": False})
    with pytest.raises(ValueError, match="订阅登录"):
        validate_provider(body)


def test_enqueue_freezes_configuration_exact_files_and_is_idempotent(tmp_path, monkeypatch):
    source = tmp_path / "chosen" / "a.mp4"
    source.parent.mkdir()
    source.write_bytes(b"data")
    sibling = source.with_name("unselected.mp4")
    sibling.write_bytes(b"data2")
    save_settings(Settings(auto_import=True))
    monkeypatch.setattr("video_tutor.analysis_control.ensure_worker", lambda: None)
    monkeypatch.setattr("video_tutor.analysis_control.provider_models", lambda _: catalog())
    monkeypatch.setattr("video_tutor.analysis_control.probe", lambda _: {"duration_ms": 60000})
    body = StudyInput(
        paths=[str(source)],
        output_directory=str(tmp_path / "out"),
        model="new-runtime-model",
        reasoning="high",
        request_id="stable-request",
    )
    first = enqueue(body)
    assert enqueue(body)["reused"]
    assert len(db.rows("SELECT * FROM study_batches")) == 1
    settings = load_settings()
    assert settings.auto_import and not settings.directories
    assert is_allowed(source, settings) and not is_allowed(sibling, settings)

    def import_fake(path, config, **kwargs):
        assert config.codex_model == "new-runtime-model"
        assert kwargs == {"auto_run": False, "stable": False}
        db.execute(
            "INSERT OR IGNORE INTO assets VALUES (?,?,?,?,?,?)", ("v_snapshot", "hash", "title", 1, "{}", time.time())
        )
        return "v_snapshot"

    monkeypatch.setattr("video_tutor.worker.import_file", import_fake)
    process_study_batches(threading.Event())
    process_study_batches(threading.Event())
    runs = db.rows("SELECT * FROM runs")
    assert len(runs) == 1
    snapshot = json.loads(runs[0]["config_json"])
    assert snapshot["analysis_provider"] == "codex" and snapshot["codex_reasoning"] == "high"
    assert snapshot["output_directory"] == str(tmp_path / "out")
    assert first["batch_id"] == "stable-request"


def test_authentication_and_new_route_errors(tmp_path, monkeypatch):
    assert TestClient(app).get("/api/v1/analysis/jobs").status_code == 401
    c = client()
    monkeypatch.setattr("video_tutor.analysis_control.codex_inventory", lambda: catalog())
    response = c.get("/api/v1/analysis/models?provider=codex")
    assert response.status_code == 200
    model = response.json()["models"][0]
    assert model["model"] == "new-runtime-model" and model["cost_multiplier"] is None
    assert (
        c.post("/api/v1/analysis/preview", json={"paths": ["missing"], "output_directory": str(tmp_path)}).status_code
        == 400
    )
    assert c.post("/api/v1/analysis/pick", json={"kind": "arbitrary-command"}).status_code == 422
    assert c.get("/api/v1/analysis/models?provider=external").status_code == 422
    assert c.get("/api/v1/runs/missing/usage").status_code == 404


def test_cost_math_does_not_double_count_cached_or_reasoning_tokens():
    assert cost_multiplier("gpt-6.1-sol") == 1
    assert cost_multiplier("gpt-6.1-sol", "priority") == 2
    assert equivalent_cost(BASE_USAGE, "unknown") is None
    assert equivalent_cost({**BASE_USAGE, "reasoning_output_tokens": 99999}, "gpt-6.1-sol") == equivalent_cost(
        BASE_USAGE, "gpt-6.1-sol"
    )


def test_codex_pipeline_exports_summary_evidence_and_receipts(tmp_path, monkeypatch):
    from video_tutor.pipeline import Pipeline
    from video_tutor.codex_pipeline import process_codex, render_notes

    source = tmp_path / "video.mp4"
    source.write_bytes(b"fake")
    image = tmp_path / "f_one.jpg"
    Image.new("RGB", (30, 30), "green").save(image)
    s = Settings(
        analysis_provider="codex",
        selected_files=[str(source)],
        output_directory=str(tmp_path / "out"),
        min_free_disk_gb=0,
    )
    db.execute("INSERT INTO assets VALUES (?,?,?,?,?,?)", ("v_c", db.file_sha(source), "主题课", 4, "{}", time.time()))
    db.create_run("v_c", s)
    p = Pipeline(claim("tester"), "tester")
    image_copy = p.folder / "f_one.jpg"
    image_copy.write_bytes(image.read_bytes())
    frame = {
        "id": "f_one",
        "path": str(image_copy),
        "time_ms": 0,
        "start_ms": 0,
        "end_ms": 60000,
        "artifact_id": db.add_artifact(p.id, image_copy, "frame"),
    }
    text = "## 对齐与层级\n\n" + "解释判断依据与适用条件。" * 20 + "\n\n![例图](images/f_one.jpg)\n"

    class Runner:
        def __init__(self, output, **kwargs):
            self.output = Path(output)
            self.output.mkdir(parents=True, exist_ok=True)

        def call(self, *args):
            return text

        def metrics(self):
            return {
                "attempts": 1,
                "unmetered_failed_attempts": 0,
                "usage": {"input_tokens": 100, "cached_input_tokens": 10, "output_tokens": 20},
            }

    def study(runner, *args, **kwargs):
        from video_tutor.study import write_json

        write_json(runner.output / "selected-frames.json", [frame])
        write_json(runner.output / "visual-gap-audit.json", {"content_gaps": []})
        (runner.output / "notes.md").write_text(text)
        (runner.output / "images").mkdir()
        (runner.output / "images/f_one.jpg").write_bytes(image.read_bytes())

    monkeypatch.setattr("video_tutor.codex_pipeline.StudyRunner", Runner)
    monkeypatch.setattr("video_tutor.codex_pipeline.optimized_study", study)
    asset = db.one("SELECT * FROM assets WHERE id=?", ("v_c",))
    doc = process_codex(
        p, asset, {"duration_ms": 60000}, source, [], [frame], {"coverage_gaps": []}, 0, 60000, time.time()
    )
    assert doc["summary_markdown"] == text
    assert doc["provenance"]["review_status"] == "generated_awaiting_source_review"
    output = next((tmp_path / "out").glob("*/*"))
    assert (output / "document.html").exists() and (output / "images/f_one.jpg").exists()
    assert (output / "detailed-records.md").exists() and (output / "usage.json").exists()
    assert "## 对齐与层级" in (output / "document.md").read_text()
    assert "## 证据索引" not in (output / "document.md").read_text()
    assert "## 证据索引" in (output / "detailed-records.md").read_text()
    html = render_notes(text + "<script>alert(1)</script>", doc["frames"])
    assert "<script>" not in html and "/artifacts/" in html
    assert db.one("SELECT status FROM runs WHERE id=?", (p.id,))["status"] == "succeeded_with_warnings"


def test_runner_uses_selected_model_subscription_and_cache_fingerprint(tmp_path):
    import sys
    from video_tutor.study import StudyRunner

    executable = tmp_path / "fake-codex"
    executable.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """import sys,json,pathlib,os
args=sys.argv[1:]
out=pathlib.Path(args[args.index('-o')+1])
out.write_text('来源与判断依据。'*30)
(out.parent/'args.json').write_text(json.dumps(args))
(out.parent/'api-key-present.txt').write_text(str(bool(os.environ.get('OPENAI_API_KEY'))))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':101,'cached_input_tokens':20,'output_tokens':30}}))
"""
    )
    executable.chmod(0o700)
    runner = StudyRunner(
        tmp_path / "out", cli=str(executable), model="runtime-model", reasoning="high", service_tier="priority"
    )
    runner.call("topic", "test source", attempts=1)
    args = json.loads((runner.output / "calls/topic/args.json").read_text())
    assert args[args.index("-m") + 1] == "runtime-model"
    assert 'model_reasoning_effort="high"' in args and 'service_tier="priority"' in args
    assert 'forced_login_method="chatgpt"' in args
    assert runner.metrics()["usage"]["input_tokens"] == 101
    assert (runner.output / "calls/topic/api-key-present.txt").read_text() == "False"
    runner.call("topic", "test source")
    assert runner.metrics()["attempts"] == 1
    other = StudyRunner(runner.output, cli=str(executable), model="another-model")
    with pytest.raises(ValueError, match="Inputs changed"):
        other.call("topic", "test source")


def test_runner_cancellation_keeps_failed_attempt_and_stops_process(tmp_path):
    import sys
    from video_tutor.study import StudyRunner
    from video_tutor.pipeline import StopWork

    executable = tmp_path / "waiting-codex"
    executable.write_text("#!" + sys.executable + "\nimport time\ntime.sleep(30)\n")
    executable.chmod(0o700)
    calls = []

    def check():
        calls.append(True)
        if len(calls) >= 3:
            raise StopWork("paused")

    runner = StudyRunner(tmp_path / "out", cli=str(executable), check=check)
    started = time.monotonic()
    with pytest.raises(StopWork):
        runner.call("waiting", "test", attempts=1)
    assert time.monotonic() - started < 5
    assert runner.metrics()["unmetered_failed_attempts"] == 1

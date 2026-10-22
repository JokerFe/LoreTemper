"""Integration tests use explicitly simulated model responses, with real files/DB/exports."""

import json
import time
from pathlib import Path

import pytest
from PIL import Image
from test_learning_quality import knowledge, rule
from test_workflows import catalog
from video_tutor import db
from video_tutor.config import Settings, data_dir
from video_tutor.study import write_json
from video_tutor.study_library import library_root, previous_notes, read_catalog, snapshot_run, verify_entry
from video_tutor.workflows import StageConfig, WorkflowInput, resolve, submit, tick


class SimulatedSkillRunner:
    def __init__(self, folder, *_):
        self.output = folder
        folder.mkdir(parents=True, exist_ok=True)

    def call(self, label, prompt):
        if label.startswith("rules-"):
            return json.dumps({"rules": [rule()], "no_rule_reason": ""}, ensure_ascii=False)
        if label.startswith("capability-map-"):
            descriptors = json.loads(prompt.split("本组所有规则ID必须且只能出现一次。\n")[1])
            return json.dumps(
                {
                    "capabilities": [
                        {
                            "id": "icons",
                            "title": "图标",
                            "use_when": "检查图标",
                            "rule_ids": [r["id"] for r in descriptors],
                        }
                    ]
                }
            )
        if label.startswith("knowledge-destinations-"):
            items = json.loads(prompt.split("\n知识：\n")[1].split("\n候选规则：\n")[0])
            rules = json.loads(prompt.split("\n候选规则：\n")[1].split("\n笔记窗口：\n")[0])
            return json.dumps(
                {
                    "items": [
                        {
                            "knowledge_id": k["id"],
                            "rule_ids": [r["id"] for r in rules],
                            "note_quote": rule()["quote"],
                            "reason": "模拟核对：该规则保留同套图标的视觉重量条件",
                        }
                        for k in items
                    ]
                },
                ensure_ascii=False,
            )
        if "-coverage" in label:
            import re

            ids = sorted(set(re.findall(r"S\d{3}-R\d{3}", prompt)))
            return json.dumps({"covered_ids": ids, "missing_ids": [], "unsupported_claims": [], "conflicts": []})
        if label == "skill-entry":
            return "---\nname: course-design-guide\ndescription: 根据有来源笔记检查同套图标的一致性。\n---\n# 图标\n[图标方法](references/capabilities/icons.md)\n保留适用条件和原始证据，实际任务效果待验证。"
        return (
            "# 图标一致性\n\n"
            + rule()["quote"]
            + "；适用于同套图标，保留不同形状的负形和光学条件，课堂示例不等于通用规范。"
        )


def completed_video(title, asset, output):
    db.execute("INSERT INTO assets VALUES (?,?,?,?,?,?)", (asset, asset + "-sha", title, 100, "{}", time.time()))
    run = db.create_run(asset, Settings())
    folder = data_dir() / "assets" / asset / "runs" / run
    (folder / "frames").mkdir(parents=True)
    image = folder / "frames/f_1.jpg"
    Image.new("RGB", (40, 30), "green").save(image)
    aid = db.add_artifact(run, image, "frame")
    write_json(folder / "study/clip-00/clip-content-inventory.json", knowledge())
    write_json(
        folder / "study/clip-00/clip-content-quality.json",
        {"automated_content_check": "passed", "coverage": {"missing_ids": [], "conflicts": []}},
    )
    write_json(folder / "study/source-transcript.json", [{"start_ms": 0, "end_ms": 5000, "raw_text": rule()["quote"]}])
    write_json(folder / "study/source-manifest.json", {"asset_id": asset, "source_sha256": asset + "-sha"})
    document = {
        "asset_id": asset,
        "title": title,
        "source_title": title,
        "duration_ms": 5000,
        "overview": {"title": title, "summary": "图标判断"},
        "chapters": [],
        "summary_markdown": "# " + title + "\n\n" + rule()["quote"] + "。12:30。\n![示例](images/f_1.jpg)",
        "frames": {"f_1": {"artifact_id": aid, "time_ms": 0}},
        "transcript": [],
        "provenance": {},
        "warnings": [],
        "user_notes": "",
    }
    db.save_document(run, document)
    db.execute("UPDATE runs SET status='succeeded' WHERE id=?", (run,))
    return run


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr("video_tutor.workflows.provider_models", lambda _: catalog())
    monkeypatch.setattr("video_tutor.workflows.ensure_workflow_worker", lambda: None)
    monkeypatch.setattr("video_tutor.workflows.TextRunner", SimulatedSkillRunner)
    monkeypatch.setattr(
        "video_tutor.workflows.default_inputs",
        lambda: {"video_paths": [], "note_paths": [], "output_directory": str(tmp_path / "output")},
    )
    monkeypatch.setattr("video_tutor.analysis_control.probe", lambda _: {"duration_ms": 5000})
    monkeypatch.setattr("video_tutor.workflows.enqueue", lambda body: {"batch_id": body.request_id})


def start_batch(tmp_path, ident, run_ids, include=False):
    source = tmp_path / (ident + ".mp4")
    source.write_bytes(b"explicit-simulated-input")
    body = WorkflowInput(
        mode="combined",
        video_paths=[str(source)],
        output_directory=str(tmp_path / "output"),
        auto_input=False,
        batch_label=ident,
        request_id=ident,
        include_previous_notes=include,
        study=StageConfig(provider="codex"),
        skill=StageConfig(provider="codex"),
    )
    submit(body)

    def job():
        return db.one("SELECT * FROM workflows WHERE id=?", (ident,))

    tick(job())
    batch = "study-" + ident
    db.execute("INSERT INTO study_batches VALUES (?,?,?)", (batch, "{}", time.time()))
    for n, run in enumerate(run_ids):
        db.execute(
            "INSERT INTO study_batch_items VALUES (?,?,?,?,?,?)",
            (ident + str(n), batch, str(source), "imported", run, None),
        )
    return job, body


def test_combined_multi_video_then_incremental_batch_keeps_all_notes_and_destinations(tmp_path, monkeypatch):
    setup(monkeypatch, tmp_path)
    runs = [completed_video("图标基础", "v_a", tmp_path), completed_video("条件与负形", "v_b", tmp_path)]
    job, _ = start_batch(tmp_path, "batch_one", runs)
    tick(job())
    assert job()["status"] == "succeeded"
    first = json.loads(job()["result_json"])
    root = library_root(tmp_path / "output")
    original = {p: db.file_sha(p) for p in (root / "entries").rglob("*") if p.is_file()}
    run = completed_video("补充案例", "v_c", tmp_path)
    job2, body2 = start_batch(tmp_path, "batch_two", [run], include=True)
    assert len(json.loads(job2()["config_json"])["previous_notes"]) == 2
    tick(job2())
    second = json.loads(job2()["result_json"])
    assert job2()["status"] == "succeeded" and second["source_count"] == 3
    assert all(db.file_sha(p) == sha for p, sha in original.items())
    catalog = read_catalog(root)
    assert len(catalog["entries"]) == 3 and len(catalog["batches"]) == 2
    assert len(catalog["batches"][1]["inherited_entry_ids"]) == 2
    assert len(previous_notes(tmp_path / "output")) == 3
    for result, count in [(first, 2), (second, 3)]:
        package = Path(result["skill_file"]).parent
        report = json.loads((package / "references/coverage/knowledge-destinations.json").read_text())
        assert len(report["items"]) == count
        assert report["counts"]["incorporated"] == count
        assert len({r["knowledge_id"] for r in report["items"]}) == count
        for row in report["items"]:
            assert all(r["source_id"] == row["source_id"] for r in row["rules"])
        assert (package / "references/sources/source-001/knowledge.json").exists()
        assert (package / "references/sources/source-001/detailed-records.md").exists()
    assert (root / "batches/batch_one/process/handoff-manifest.json").exists()
    assert "图标基础" in (root / "index.html").read_text()
    frozen = json.loads((data_dir() / "workflows/batch_two/handoff.json").read_text())
    assert len(frozen) == 3
    assert len(resolve(body2)["previous_notes"]) == 3  # a new preview sees new material, existing handoff is frozen
    approved = [n["entry_id"] for n in json.loads(job2()["config_json"])["previous_notes"]]
    assert len(resolve(body2.model_copy(update={"previous_entry_ids": approved}))["previous_notes"]) == 2
    with pytest.raises(ValueError, match="重新预览"):
        previous_notes(tmp_path / "output", ["unknown/entry"])


def test_snapshot_tampering_is_detected_and_versions_do_not_overwrite(tmp_path):
    run = completed_video("版本一", "v_a", tmp_path)
    first = snapshot_run(tmp_path / "out", run)
    folder = library_root(tmp_path / "out") / first["directory"]
    note = (folder / "notes.md").read_text()
    doc = db.latest_document(run)["body"]
    doc["summary_markdown"] += "\n新增有来源说明。"
    db.save_document(run, doc)
    second = snapshot_run(tmp_path / "out", run)
    assert second["entry_id"] != first["entry_id"]
    assert (folder / "notes.md").read_text() == note
    (folder / "notes.md").write_text("tampered")
    with pytest.raises(ValueError, match="快照已变化"):
        verify_entry(folder)


def test_failure_keeps_completed_notes_and_retry_does_not_duplicate_library(tmp_path, monkeypatch):
    setup(monkeypatch, tmp_path)
    run = completed_video("<script>alert(1)</script>", "v_a", tmp_path)
    job, _ = start_batch(tmp_path, "batch_failure", [run])
    real = __import__("video_tutor.workflows", fromlist=["generate_skill"]).generate_skill

    def fail(*args):
        write_json(data_dir() / "workflows/batch_failure/inference/failed-attempt.json", {"error": "simulated"})
        raise ValueError("模拟生成失败")

    monkeypatch.setattr("video_tutor.workflows.generate_skill", fail)
    with pytest.raises(ValueError, match="模拟生成失败"):
        tick(job())
    root = library_root(tmp_path / "output")
    assert len(read_catalog(root)["entries"]) == 1
    assert (root / "batches/batch_failure/process/inference/failed-attempt.json").exists()
    assert "<script>alert(1)</script>" not in (root / "index.html").read_text()
    monkeypatch.setattr("video_tutor.workflows.generate_skill", real)
    tick(job())
    assert job()["status"] == "succeeded"
    assert len(read_catalog(root)["entries"]) == 1
    assert (root / "batches/batch_failure/process/inference/failed-attempt.json").exists()


def test_nonexistent_foreign_rule_and_missing_knowledge_cannot_pass(tmp_path):
    from video_tutor.knowledge_lineage import map_knowledge

    run = completed_video("来源", "v_a", tmp_path)
    entry = snapshot_run(tmp_path / "out", run)
    from video_tutor.study_library import entry_note

    note = entry_note(library_root(tmp_path / "out"), entry)
    package = tmp_path / "package"
    write_json(package / "references/coverage/rules.json", [{"rule_id": "S001-R001"}])

    class Bad:
        def call(self, label, prompt):
            item = json.loads(Path(note["knowledge_path"]).read_text())["items"][0]
            return json.dumps(
                {
                    "items": [
                        {
                            "knowledge_id": item["id"],
                            "rule_ids": ["S002-R001"],
                            "note_quote": rule()["quote"],
                            "reason": "bad",
                        }
                    ]
                }
            )

    with pytest.raises(ValueError, match="非本来源"):
        map_knowledge(Bad(), package, [note], [{**rule(), "id": "S001-R001", "source_id": "S001"}], {})


@pytest.mark.parametrize("quote,expected", [(rule()["quote"], "retained_in_notes"), ("", "needs_review")])
def test_knowledge_not_promoted_to_rule_remains_visible(tmp_path, quote, expected):
    from video_tutor.knowledge_lineage import map_knowledge
    from video_tutor.study_library import entry_note

    run = completed_video("案例也保留", "v_case", tmp_path)
    entry = snapshot_run(tmp_path / "out", run)
    note = entry_note(library_root(tmp_path / "out"), entry)
    package = tmp_path / "package"
    write_json(package / "references/coverage/rules.json", [])

    class NoRules:
        def call(self, label, prompt):
            items = json.loads(Path(note["knowledge_path"]).read_text())["items"]
            return json.dumps(
                {
                    "items": [
                        {
                            "knowledge_id": k["id"],
                            "rule_ids": [],
                            "note_quote": quote,
                            "reason": "测试：保持案例或待核查，不强行升级规则",
                        }
                        for k in items
                    ]
                }
            )

    report = map_knowledge(NoRules(), package, [note], [], {})
    assert report["counts"][expected] == entry["knowledge_count"]
    assert len(report["items"]) == entry["knowledge_count"]
    assert (package / "references/coverage/knowledge-destinations.html").is_file()


def test_missing_inventory_and_reused_checkpoint_are_explicit(tmp_path):
    run = completed_video("缓存来源", "v_cache", tmp_path)
    write_json(data_dir() / "assets/v_cache/runs" / run / "study/entry.json", {"auxiliary": "应纳入完整指纹清单"})
    foreign = data_dir() / "foreign-run/retained-checkpoint.json"
    write_json(foreign, {"transcription": "原始模型回执"})
    db.execute(
        "INSERT INTO units VALUES (?,?,?,?,?,?,?)",
        (run, "transcribe", "0", "input-sha", str(foreign.relative_to(data_dir())), db.file_sha(foreign), 0),
    )
    entry = snapshot_run(tmp_path / "out", run)
    folder = library_root(tmp_path / "out") / entry["directory"]
    assert any(f["path"] == "artifacts/study/entry.json" for f in entry["files"])
    assert (folder / "reused-artifacts/foreign-run/retained-checkpoint.json").read_bytes() == foreign.read_bytes()
    records = json.loads((folder / "database-records.json").read_text())
    assert len(records["reused_dependencies"]) == 1
    from video_tutor.knowledge_lineage import map_knowledge

    package = tmp_path / "package"
    write_json(package / "references/coverage/rules.json", [])
    report = map_knowledge(None, package, [{"path": str(folder / "notes.md")}], [], {})
    assert len(report["missing_inventories"]) == 1 and report["items"] == []


def test_note_output_directories_keep_independent_indexes_and_history(tmp_path):
    from video_tutor.study_library import register_batch

    left, right = tmp_path / "project-a", tmp_path / "project-b"
    a = snapshot_run(left, completed_video("A目录独有内容", "v_a", tmp_path))
    b = snapshot_run(right, completed_video("B目录独有内容", "v_b", tmp_path))
    register_batch(left, {"id": "batch-001"}, {}, [a])
    register_batch(right, {"id": "batch-001"}, {}, [b])
    assert [n["title"] for n in previous_notes(left)] == ["A目录独有内容"]
    assert [n["title"] for n in previous_notes(right)] == ["B目录独有内容"]
    assert "B目录独有内容" not in (library_root(left) / "index.html").read_text()
    assert "A目录独有内容" not in (library_root(right) / "index.html").read_text()
    with pytest.raises(ValueError, match="重新预览"):
        previous_notes(left, [b["entry_id"]])

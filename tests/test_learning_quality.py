import json
from pathlib import Path
import time
import pytest
from video_tutor import db
from video_tutor.config import data_dir
from video_tutor.learning_quality import finalize_notes, extract_rules, coverage_validator, validate_package_links
from video_tutor.workflows import generate_skill


def knowledge():
    return {
        "items": [
            {
                "id": "K001",
                "topic": "视觉重量",
                "core": "光学一致",
                "basis": "轮廓不等于重量",
                "method": "比较负形",
                "conditions": "同套图标",
                "case_or_parameter": "对比两个图标",
                "quote": "通过轮廓、负形与视觉重量判断一致性",
            }
        ]
    }


def rule():
    return {
        "capability": "icons",
        "capability_title": "图标一致性",
        "claim": "以视觉重量判断成套性",
        "rationale": "几何尺寸相同未必视觉等重",
        "method": "比较轮廓与负形",
        "conditions": "同套图标",
        "exceptions": "示例不是固定尺寸配方",
        "evidence_type": "teacher_suggestion",
        "quote": "通过轮廓、负形与视觉重量判断一致性",
        "source_location": "12:30",
    }


class NoteRunner:
    def __init__(self, folder, unresolved=False):
        self.output = folder
        folder.mkdir()
        self.labels = []
        self.unresolved = unresolved

    def call(self, label, prompt):
        self.labels.append(label)
        if label.endswith("-inventory"):
            return json.dumps(knowledge(), ensure_ascii=False)
        if "-coverage" in label:
            missing = self.unresolved or "after-repair" not in label
            return json.dumps(
                {
                    "covered_ids": [] if missing else ["K001"],
                    "missing_ids": ["K001"] if missing else [],
                    "unsupported_claims": [],
                    "conflicts": [],
                }
            )
        return "# 视觉重量\n\n通过轮廓、负形与视觉重量判断一致性，适用于同套图标，示例不能推广为固定尺寸。"


def test_notes_repair_core_coverage_and_keep_audit_separate(tmp_path):
    runner = NoteRunner(tmp_path / "quality")
    notes, report = finalize_notes(runner, "原稿只说统一风格", "通过轮廓、负形与视觉重量判断一致性。来源12:30。")
    assert "负形" in notes and report["automated_content_check"] == "passed"
    assert report["source_review"] == "requires_independent_source_review"
    assert (runner.output / "knowledge-coverage-after-repair.json").exists()
    assert (runner.output / "knowledge-before-quality.md").read_text() == "原稿只说统一风格"
    assert "covered_ids" not in notes


def test_unresolved_core_gaps_are_not_published_as_complete(tmp_path):
    runner = NoteRunner(tmp_path / "quality", True)
    with pytest.raises(ValueError, match="仍有核心遗漏"):
        finalize_notes(runner, "不完整总结", "通过轮廓、负形与视觉重量判断一致性")
    assert len([label for label in runner.labels if label.endswith("-revision")]) == 1


def test_rule_quote_and_timestamp_must_exist_and_audit_cannot_hide_ids(tmp_path):
    class Runner:
        def call(self, *_):
            return json.dumps({"rules": [{**rule(), "quote": "来源没有这句话"}], "no_rule_reason": ""})

    with pytest.raises(ValueError, match="quote"):
        extract_rules(Runner(), "rules-1", "S001", "通过轮廓、负形与视觉重量判断一致性。12:30")
    for value in [
        {"covered_ids": ["K001", "K001"], "missing_ids": [], "unsupported_claims": [], "conflicts": []},
        {"covered_ids": [], "missing_ids": [], "unsupported_claims": [], "conflicts": []},
    ]:
        with pytest.raises(ValueError):
            coverage_validator(json.dumps(value), ["K001"])


def test_rule_repair_identifies_the_unmatched_quote():
    class Runner:
        def __init__(self):
            self.prompts = []

        def call(self, label, prompt):
            self.prompts.append(prompt)
            quote = "被改写的引用" if len(self.prompts) == 1 else rule()["quote"]
            return json.dumps({"rules": [{**rule(), "quote": quote}], "no_rule_reason": ""})

    runner = Runner()
    result = extract_rules(runner, "rules-1", "S001", "通过轮廓、负形与视觉重量判断一致性。12:30")
    assert result["rules"][0]["quote"] == rule()["quote"]
    assert "第1条" in runner.prompts[1] and "被改写的引用" in runner.prompts[1]


def test_layered_skill_has_complete_rules_conditions_sources_and_routing(tmp_path, monkeypatch):
    note = tmp_path / "notes.md"
    note.write_text("# 图标\n通过轮廓、负形与视觉重量判断一致性。12:30。")
    (tmp_path / "images").mkdir()
    (tmp_path / "images/demo.jpg").write_bytes(b"fixture-image")
    note.write_text(note.read_text() + "\n![示例](images/demo.jpg)")
    from video_tutor.workflows import note_files

    notes = note_files([str(note)])
    config = {
        "quality_profile": "joker-ui-v2",
        "skill_name": "test-guide",
        "skill_goal": "图标方法",
        "output_directory": str(tmp_path / "out"),
        "skill": {"provider": "codex", "model": "test", "reasoning": "medium", "service_tier": "default"},
    }
    db.execute(
        "INSERT INTO workflows VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("wf_quality", "skill", "running", "prepare", db.dumps(config), None, None, None, time.time(), time.time()),
    )

    class Runner:
        def __init__(self, folder, *_):
            self.output = folder

        def call(self, label, prompt):
            if label.startswith("rules-"):
                return json.dumps({"rules": [rule()], "no_rule_reason": ""}, ensure_ascii=False)
            if label.startswith("capability-map-"):
                return json.dumps(
                    {
                        "capabilities": [
                            {
                                "id": "icons",
                                "title": "图标一致性",
                                "use_when": "图标不成套时",
                                "rule_ids": ["S001-R001"],
                            }
                        ]
                    }
                )
            if "-coverage" in label:
                return json.dumps(
                    {"covered_ids": ["S001-R001"], "missing_ids": [], "unsupported_claims": [], "conflicts": []}
                )
            if label == "skill-entry":
                return "---\nname: test-guide\ndescription: 基于来源笔记指导同套图标的视觉重量判断。\n---\n\n# 图标设计\n\n按问题读取[图标能力](references/capabilities/icons.md)，保留条件、例外与原始证据，不把建议当实测。\n"
            return "# 图标一致性\n\n同套图标中比较轮廓、负形与视觉重量；几何尺寸相同不保证视觉等重。此处是课程建议，不是固定尺寸规范；仍需回看原图与实际使用情境。"

    monkeypatch.setattr("video_tutor.workflows.TextRunner", Runner)
    result = generate_skill({"id": "wf_quality"}, config, notes)
    package = Path(result["skill_file"]).parent
    coverage = json.loads((package / "references/coverage/rules.json").read_text())
    assert [r["rule_id"] for r in coverage] == ["S001-R001"]
    conditions = (package / "references/rules/icons.md").read_text()
    assert "同套图标" in conditions and "示例不是固定尺寸配方" in conditions
    assert (package / "references/sources/source-001/images/demo.jpg").exists()
    assert (package / "references/coverage/generation-report.json").exists()
    assert (package / "references/coverage/validation-plan.md").exists()
    assert result["generation_report"]["rule_count"] == 1
    assert result["behavioral_validation"] == "not_run" and not result["installed"]
    validate_package_links(package)
    (package / "references/capabilities/icons.md").write_text("[missing](../../../not-present.md)")
    with pytest.raises(ValueError, match="越界"):
        validate_package_links(package)


def test_all_note_images_must_reference_selected_local_evidence(tmp_path):
    from video_tutor.thematic_study import validate_image_references

    (tmp_path / "images").mkdir()
    (tmp_path / "images/f_1.jpg").write_bytes(b"frame")
    validate_image_references("![证据](images/f_1.jpg)", tmp_path, {"f_1"})
    for target in ("https://example.com/forged.jpg", "../other.jpg", "images/f_unknown.jpg"):
        with pytest.raises(ValueError):
            validate_image_references(f"![未取证]({target})", tmp_path, {"f_1"})


def test_local_summary_preserves_thematic_main_note_and_quality_reports(tmp_path, monkeypatch):
    from video_tutor.config import Settings, save_settings
    from video_tutor.pipeline import Pipeline
    from video_tutor.worker import import_file, claim
    from video_tutor.schemas import Observation, Chapter, Overview
    from video_tutor.note_quality import ContentResponse
    from test_media import create_fixture

    source = create_fixture(tmp_path / "video")
    settings = Settings(
        directories=[str(source.parent)], learning_quality="joker-ui-v2", require_ac_power=False, min_free_disk_gb=0
    )
    save_settings(settings)
    asset = import_file(source, settings, stable=False)
    monkeypatch.setattr(
        "video_tutor.pipeline.model_inventory",
        lambda _: [
            {"name": settings.vision_model, "digest": "vision"},
            {"name": settings.text_model, "digest": "text"},
        ],
    )
    summary = "# 视觉重量\n\n对同套图标先比较轮廓与负形，说明判断依据和适用条件；示例数值不作为通用配方。"

    class LLM:
        def __init__(self, *_):
            pass

        def unload(self, *_):
            pass

        def chat(self, model, schema, system, payload, frames=None, **kwargs):
            if schema is Observation:
                return Observation(description="测试画面", frame_ids=[f["id"] for f in frames]).model_dump()
            if schema is Chapter:
                return Chapter(
                    title="观察图标",
                    summary="测试材料",
                    steps=[
                        {
                            "title": "观察",
                            "action": "测试画面",
                            "evidence_ids": payload["observations"][0]["frame_ids"],
                            "evidence_type": "visual",
                            "start_ms": payload["start_ms"],
                            "end_ms": payload["end_ms"],
                        }
                    ],
                ).model_dump()
            if schema is Overview:
                return Overview(
                    title="图标课", summary="图标判断", summary_evidence_ids=payload["chapters"][0]["evidence_ids"]
                ).model_dump()
            assert schema is ContentResponse and kwargs["context_size"] == 32768
            task = payload["task"]
            if "建立核心知识清单" in task:
                data = knowledge()
                data["items"][0]["quote"] = "测试画面"
                return {"content": json.dumps(data, ensure_ascii=False)}
            if "逐项比较核心知识清单" in task:
                return {
                    "content": json.dumps(
                        {"covered_ids": ["K001"], "missing_ids": [], "unsupported_claims": [], "conflicts": []}
                    )
                }
            return {"content": summary}

    monkeypatch.setattr("video_tutor.pipeline.LocalLLM", LLM)
    run = claim("local-quality")
    doc = Pipeline(run, "local-quality").execute()
    assert doc["asset_id"] == asset and doc["summary_markdown"] == summary
    root = data_dir() / "assets" / asset / "runs" / run["id"] / "exports/v1"
    assert "视觉重量" in (root / "document.md").read_text()
    assert "### 00:00:00 观察" not in (root / "document.md").read_text()
    assert "测试画面" in (root / "detailed-records.md").read_text()
    assert list((root / "quality").rglob("*quality.json"))

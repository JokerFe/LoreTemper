"""Connect the existing media checkpoints to the thematic Codex study workflow."""

from __future__ import annotations
import json
import re
import shutil
import time
from pathlib import Path
from markdown_it import MarkdownIt
from . import db
from .config import atomic_write
from .exporting import export_document
from .study import StudyRunner, write_json
from .thematic_study import RULES, optimized_study, validate_image_references


def publish_output(pipeline, export_folder):
    from .analysis_control import output_path

    root = output_path(pipeline.settings.output_directory)
    title = db.one("SELECT title FROM assets WHERE id=?", (pipeline.run["asset_id"],))["title"]
    safe = re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", title).strip("-")[:70] or "video"
    destination = root / f"{safe}-{pipeline.run['asset_id']}" / pipeline.id
    destination.mkdir(parents=True, exist_ok=True)
    # A run owns its subfolder; exports from different runs never overwrite each other.
    shutil.copytree(export_folder, destination, dirs_exist_ok=True)
    evidence = pipeline.folder / "study"
    if evidence.exists():
        shutil.copytree(evidence, destination / "evidence", dirs_exist_ok=True)
    write_json(
        destination / "output-manifest.json",
        {
            "run_id": pipeline.id,
            "asset_id": pipeline.run["asset_id"],
            "provider": pipeline.settings.analysis_provider,
            "model": pipeline.settings.codex_model
            if pipeline.settings.analysis_provider == "codex"
            else pipeline.settings.text_model,
            "review_status": "generated_awaiting_source_review",
        },
    )
    pipeline.output_folder = destination
    db.event(pipeline.id, "output", str(destination))
    return destination


def render_notes(markdown, frames):
    renderer = MarkdownIt("commonmark", {"html": False}).enable("table")
    tokens = renderer.parse(markdown)
    by_name = {f"{fid}.jpg": f["artifact_id"] for fid, f in frames.items()}

    def walk(items):
        for token in items:
            if token.type == "image":
                aid = by_name.get(Path(token.attrGet("src") or "").name)
                token.attrSet("src", "/artifacts/" + aid if aid else "")
            if token.children:
                walk(token.children)

    walk(tokens)
    return renderer.renderer.render(tokens, renderer.options, {})


def process_codex(p, asset, media, source, transcript, frames, sampling, start, end, started):
    root = p.folder / "study"
    root.mkdir(exist_ok=True)
    write_json(root / "source-transcript.json", transcript)
    write_json(
        root / "source-manifest.json",
        {
            "asset_id": asset["id"],
            "source_sha256": asset["sha256"],
            "source_path": str(source),
            "start_ms": start,
            "end_ms": end,
        },
    )
    from .learning_quality import PROFILE, NOTE_POLICY, finalize_notes

    quality_enabled = p.settings.learning_quality == PROFILE
    runners, notes, selected = [], [], {}
    spans = list(range(start, end, 1200000))
    try:
        for n, a in enumerate(spans):
            p.stage("thematic_study", n, len(spans))
            b = min(a + 1200000, end)
            clip_frames = [f for f in frames if a <= f["time_ms"] < b]
            if not clip_frames:
                raise ValueError(f"{a // 1000}秒处缺少候选截图，无法完成主题取证")
            folder = root / f"clip-{n + 1:03}"
            runner = StudyRunner(
                folder,
                model=p.settings.codex_model,
                reasoning=p.settings.codex_reasoning,
                service_tier=p.settings.codex_service_tier,
                check=p.check,
            )
            runners.append(runner)
            optimized_study(runner, transcript, None, a, b, source_frames=frames)
            # Apply source-repair findings and content gaps to the main summary before course synthesis.
            audit = json.loads((folder / "visual-gap-audit.json").read_text())
            repairs = "\n\n".join(
                f.read_text() for f in sorted(folder.glob("targeted-evidence-repair-*-observations.md"))
            )
            original = (folder / "notes.md").read_text()
            prompt = (
                RULES
                + """依据本段笔记、来源核验和内容缺口完成主题总结修订。
保留核心思想、判断依据、方法、条件、案例与必要参数；不设字数或图片数。
合并重复讲解，不逐字转述，不拼接审阅表。来源不确定的内容保留边界。
只输出修订后的完整学习笔记。只使用已取证的images/图片路径。\n原笔记：\n"""
            )
            prompt += (
                original
                + "\n来源补查：\n"
                + repairs
                + "\n内容缺口：\n"
                + json.dumps(audit.get("content_gaps", []), ensure_ascii=False)
            )
            # Preserve the full source speech for factual repair.
            from .thematic_study import compact_speech

            prompt += "\n完整讲解：\n" + compact_speech(transcript, a, b)
            revised = runner.call("source-review-revision", prompt)
            selection = folder / "selected-frames-after-repair.json"
            if not selection.exists():
                selection = folder / "selected-frames.json"
            local = json.loads(selection.read_text())
            validate_image_references(revised, folder, {f["id"] for f in local})
            if quality_enabled:
                from .thematic_study import compact_speech

                evidence_cards = (
                    (folder / "evidence-cards.md").read_text() if (folder / "evidence-cards.md").exists() else ""
                )
                revised, _ = finalize_notes(
                    runner,
                    revised,
                    compact_speech(transcript, a, b) + "\n" + evidence_cards + "\n" + repairs,
                    label="clip-content",
                    image_validator=lambda md: validate_image_references(md, folder, {f["id"] for f in local}),
                )
            atomic_write(folder / "notes-revised.md", revised)
            for frame in local:
                selected[frame["id"]] = frame
            notes.append(revised)
            p.progress(n + 1, len(spans))
        p.stage("summarize")
        synthesis = StudyRunner(
            root / "synthesis",
            model=p.settings.codex_model,
            reasoning=p.settings.codex_reasoning,
            service_tier=p.settings.codex_service_tier,
            check=p.check,
        )
        runners.append(synthesis)
        # Bounded hierarchical synthesis for long recordings; raw part notes remain inspectable.
        level = 0
        while len(notes) > 1:
            groups, group, chars = [], [], 0
            for note in notes:
                if group and (chars + len(note) > 45000 or len(group) >= 6):
                    groups.append(group)
                    group, chars = [], 0
                group.append(note)
                chars += len(note)
            if group:
                groups.append(group)
            if len(groups) == len(notes):
                raise ValueError("单段笔记过长，整课综合无法安全收敛；已保留分段资料")
            reduced = []
            for index, group in enumerate(groups):
                prompt = (
                    RULES
                    + """将以下已取证的连续课程笔记整合为按知识主题组织的完整内容总结。
合并重复知识、保留核心思想、判断依据、方法、条件、必要案例与关键参数。
不设字数或截图数量，不逐段拼接、不按时间流水记账、不生成最终Skill。
保留代表性时间戳和实际images/图片引用。不能用候选规则或课程示例代替通用事实。
只输出主题总结正文。\n"""
                    + "\n\n".join(group)
                )
                draft = synthesis.call(
                    f"course-synthesis-{level}-{index}", prompt + (NOTE_POLICY if quality_enabled else "")
                )
                if quality_enabled:
                    draft, _ = finalize_notes(synthesis, draft, "\n\n".join(group), label=f"course-{level}-{index}")
                reduced.append(draft)
            notes = reduced
            level += 1
        summary = notes[0]
        image_folder = synthesis.output / "images"
        image_folder.mkdir(exist_ok=True)
        for fid, frame in selected.items():
            shutil.copy2(frame["path"], image_folder / f"{fid}.jpg")
        validate_image_references(summary, synthesis.output, set(selected))
        write_json(root / "selected-frames.json", list(selected.values()))
        p.warnings.append("自动生成与补图核验已完成，仍待来源审阅；未生成最终 Skill。")
        overview = {
            "title": asset["title"],
            "summary": "按主题整理的课程内容总结，含来源截图与时间戳；待来源审阅。",
            "summary_evidence_ids": [],
            "goals": [],
            "prerequisites": [],
            "key_points": [],
            "verification": [],
            "unresolved_items": [],
        }
        doc = {
            "schema_version": 1,
            "asset_id": asset["id"],
            "run_id": p.id,
            "source_title": asset["title"],
            "duration_ms": media["duration_ms"],
            "overview": overview,
            "summary_markdown": summary,
            "chapters": [],
            "transcript": transcript,
            "frames": {fid: {k: v for k, v in f.items() if k != "path"} for fid, f in selected.items()},
            "observations": [],
            "warnings": p.warnings,
            "user_notes": "",
            "coverage": {k: v for k, v in sampling.items() if k != "frames"},
            "provenance": {
                "source_sha256": asset["sha256"],
                "source_path": str(source),
                "settings": p.settings.model_dump(),
                "run_options": p.options,
                "pipeline_version": "codex-thematic-v1",
                "note_quality_profile": p.settings.learning_quality,
                "started_at": started,
                "review_status": "generated_awaiting_source_review",
                "skill_generated": False,
            },
        }
        previous = db.latest_document(p.id)
        if previous:
            doc["user_notes"] = previous["body"].get("user_notes", "")
        version = db.save_document(p.id, doc)
        p.stage("export")
        export_folder = p.folder / "exports" / f"v{version}"
        export_document(p.id, doc, export_folder, version)
        if p.settings.output_directory:
            publish_output(p, export_folder)
        from .retrieval import index_document

        index_document(doc)
        p.check()
        db.execute(
            "UPDATE runs SET status='succeeded_with_warnings',stage='complete',warnings_json=?,finished_at=?,lease_owner=NULL,lease_expires=NULL WHERE id=? AND generation=?",
            (db.dumps(p.warnings), time.time(), p.id, p.generation),
        )
        db.event(p.id, "complete", "主题笔记已生成，待来源审阅")
        return doc
    finally:
        usage, missing, attempts = {}, 0, 0
        for runner in runners:
            metrics = runner.metrics()
            attempts += metrics["attempts"]
            missing += metrics["unmetered_failed_attempts"]
            for key, value in metrics["usage"].items():
                usage[key] = usage.get(key, 0) + value
        write_json(
            p.folder / "codex-usage.json",
            {
                "usage": usage,
                "attempts": attempts,
                "unmetered_failed_attempts": missing,
                "model": p.settings.codex_model,
                "service_tier": p.settings.codex_service_tier,
            },
        )
        if p.settings.output_directory:
            # The durable primary receipt stays in application data even if the chosen drive disconnects.
            try:
                from .analysis_control import output_path

                if "export_folder" in locals():
                    root_output = output_path(p.settings.output_directory)
                    safe = re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", asset["title"]).strip("-")[:70] or "video"
                    receipt_folder = root_output / f"{safe}-{asset['id']}" / p.id
                    if receipt_folder.exists():
                        shutil.copy2(p.folder / "codex-usage.json", receipt_folder / "usage.json")
            except OSError:
                pass

from __future__ import annotations
import html
import re
import shutil
import zipfile
from markdown_it import MarkdownIt
from .config import atomic_write
from . import db


def stamp(ms, srt=False):
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, milli = divmod(rem, 1000)
    return f"{h:02}:{m:02}:{s:02}" + (f",{milli:03}" if srt else "")


def srt(segments):
    return (
        "\n\n".join(
            f"{i + 1}\n{stamp(s['start_ms'], True)} --> {stamp(s['end_ms'], True)}\n{s['raw_text']}"
            for i, s in enumerate(segments)
        )
        + "\n"
    )


def escape_md(text):
    return re.sub(r"([\\`*{}\[\]<>#|])", r"\\\1", str(text))


def markdown(doc):
    e = escape_md
    lines = [
        f"# {e(doc['overview']['title'])}",
        doc["overview"]["summary"],
        "",
        f"来源：{e(doc['source_title'])} · 时长 {stamp(doc['duration_ms'])}",
        "说明：截图与原文是证据；推断和模糊内容列在待核实项。",
        "",
    ]
    if doc.get("summary_markdown"):
        lines += [doc["summary_markdown"], "", "---", "完整字幕与证据索引：[独立查证材料](detailed-records.md)"]
        if doc.get("warnings"):
            lines += ["", "来源审阅状态：" + "；".join(doc["warnings"])]
        if doc.get("user_notes"):
            lines += ["", "## 人工补充笔记", "", e(doc["user_notes"])]
        return "\n".join(lines) + "\n"
    for key, title in [
        ("goals", "学习目标"),
        ("prerequisites", "前置条件"),
        ("key_points", "核心要点"),
        ("verification", "验证方式"),
    ]:
        points = doc["overview"].get(key, [])
        if points:
            lines += (
                ["## " + title, ""]
                + [f"- {e(p['text'])}（证据：{', '.join(p['evidence_ids'])}）" for p in points]
                + [""]
            )
    for ch in doc["chapters"]:
        lines += ["## " + e(ch["title"]), "", e(ch["summary"]), ""]
        for i, step in enumerate(ch["steps"]):
            lines += [f"### {stamp(step['start_ms'])} {e(step['title'])}", "", e(step["action"]), ""]
            if step.get("before_state"):
                lines += ["操作前：" + e(step["before_state"]), ""]
            if step.get("after_state"):
                lines += ["结果：" + e(step["after_state"]), ""]
            for p in step.get("parameters", []):
                lines += [
                    f"- {e(p['name'])}：{e(p['value']) if p['value'] is not None else '无法确认'} {e(p.get('uncertainty', ''))}"
                ]
            lines += ["", "证据：" + ", ".join(step["evidence_ids"]) + "；类型：" + step["evidence_type"], ""]
            for fid in [x for x in step["evidence_ids"] if x in doc["frames"]][:2]:
                lines += [f"![{stamp(doc['frames'][fid]['time_ms'])}](images/{fid}.jpg)", ""]
            for item in step.get("uncertainties", []):
                lines += ["- 待核实：" + e(item)]
    unknown = list(
        dict.fromkeys(
            doc.get("warnings", [])
            + doc["overview"].get("unresolved_items", [])
            + [x for c in doc["chapters"] for x in c.get("unresolved_items", [])]
        )
    )
    if unknown:
        lines += ["", "## 待核实与覆盖限制", ""] + ["- " + e(x) for x in unknown]
    lines += ["", "## 证据索引", ""]
    for fid, f in doc["frames"].items():
        lines += [f"- {fid}：{stamp(f['time_ms'])}，图片 images/{fid}.jpg"]
    for t in doc["transcript"]:
        lines += [f"- {t['id']}：{stamp(t['start_ms'])} {e(t['raw_text'])}"]
    if doc.get("user_notes"):
        lines += ["", "## 人工补充笔记", "", e(doc["user_notes"])]
    return "\n".join(lines) + "\n"


def export_document(run_id, doc, folder, version=1, *, register=True):
    folder.mkdir(parents=True, exist_ok=True)
    images = folder / "images"
    images.mkdir(exist_ok=True)
    for fid, frame in doc["frames"].items():
        artifact = db.one("SELECT path FROM artifacts WHERE id=?", (frame["artifact_id"],))
        if artifact:
            from .config import data_dir

            shutil.copy2(data_dir() / artifact["path"], images / (fid + ".jpg"))
    if doc.get("summary_markdown"):
        detailed = {k: v for k, v in doc.items() if k != "summary_markdown"}
        atomic_write(folder / "detailed-records.md", markdown(detailed))
    quality_folder = folder / 'quality'
    from .config import data_dir
    run_folder = data_dir() / 'assets' / doc['asset_id'] / 'runs' / run_id
    for root in (run_folder/'study', run_folder/'note-quality'):
        if root.exists():
            for report in root.rglob('*quality.json'):
                destination = quality_folder / root.name / report.relative_to(root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(report, destination)
            for report in root.rglob('*inventory.json'):
                destination = quality_folder / root.name / report.relative_to(root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(report, destination)
            for report in root.rglob('*coverage*.json'):
                destination = quality_folder / root.name / report.relative_to(root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(report, destination)
    md = markdown(doc)
    atomic_write(folder / "document.md", md)
    renderer = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")
    body = renderer.render(md)
    css = "body{max-width:900px;margin:40px auto;padding:0 24px;font:16px/1.8 system-ui;color:#19312b;background:#fafbf8}img{max-width:100%;border:1px solid #ddd;border-radius:8px}pre{overflow:auto;padding:16px;background:#eee}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:8px}h1,h2,h3{line-height:1.4}"
    atomic_write(
        folder / "document.html",
        f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{html.escape(doc["overview"]["title"])}</title><style>{css}</style><body>{body}</body></html>',
    )
    atomic_write(folder / "document.json", db.dumps(doc))
    atomic_write(folder / "transcript.srt", srt(doc["transcript"]))
    atomic_write(folder / "provenance.json", db.dumps(doc["provenance"]))
    atomic_write(folder / "README.txt", "此导出包含图文笔记、原始转写和截图。原视频没有复制到包内。HTML 可离线打开。\n")
    zip_path = folder / f"video-notes-v{version}.zip"
    with zipfile.ZipFile(zip_path.with_suffix(".tmp"), "w", zipfile.ZIP_DEFLATED) as z:
        for f in folder.rglob("*"):
            if f.is_file() and f.suffix not in (".zip", ".tmp"):
                z.write(f, str(f.relative_to(folder)))
    zip_path.with_suffix(".tmp").replace(zip_path)
    return {p.name: db.add_artifact(run_id, p, "export") for p in folder.iterdir() if p.is_file()} if register else {}

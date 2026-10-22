"""Add curated historical notes to a library without rewriting their sources.

The note/HTML/review records are immutable copies; images, videos and detailed
process materials remain linked to their original locations. No model runs.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from . import db
from .study import write_json
from .study_library import (
    _safe_name,
    file_manifest,
    library_root,
    read_catalog,
    register_batch,
    verify_entry,
)
from .study_library import (
    notes_directory as check_notes_directory,
)


def relative_url(path, directory):
    return quote(os.path.relpath(path, directory).replace(os.sep, "/"), safe="/")


def prepare(catalog_path, project, notes_directory):
    catalog = json.loads(catalog_path.read_text())
    prepared = []
    for batch in catalog["batches"]:
        _safe_name(batch["id"])
        videos = []
        for video in batch["videos"]:
            if not video.get("reviewed_note"):
                continue
            _safe_name(video["asset_id"])
            note = (project / video["reviewed_note"]).resolve()
            if not note.is_relative_to(notes_directory):
                continue
            status_path = (project / video["codex_study"]["status_file"]).resolve()
            status = json.loads(status_path.read_text())
            if status.get("status") != "completed":
                raise ValueError("历史学习尚未完成：" + video["title"])
            originals = {"notes.md": note, "original-notes.html": note.with_suffix(".html"), "status.json": status_path}
            for name in ("review.md", "report.md"):
                path = note.parent / name
                if path.is_file():
                    originals[name] = path
            fingerprints = {name: db.file_sha(path) for name, path in originals.items()}
            videos.append((video, originals, fingerprints, status))
        if videos:
            prepared.append((batch, videos))
    return prepared


def snapshot(root, project, catalog_path, batch, item):
    video, originals, fingerprints, status = item
    note = originals["notes.md"]
    provenance = {
        "source_catalog": str(catalog_path),
        "original_batch_id": batch["id"],
        "original_batch_label": batch["label"],
        "original_video_record": video,
        "original_files": [{"path": str(p), "sha256": fingerprints[n], "snapshot": n} for n, p in originals.items()],
        "source_video_availability": [{"path": s, "exists": Path(s).is_file()} for s in video.get("sources", [])],
        "retention": "Notes, HTML and review records copied; images, videos and process materials referenced in place.",
        "knowledge_inventory": "not_imported; legacy review is not a new knowledge-to-rule audit",
    }
    key = "historical-" + db.digest(provenance)[:16]
    folder = root / "entries" / video["asset_id"] / key
    if (folder / "entry.json").exists():
        return verify_entry(folder)
    temp = folder.with_name(key + ".partial-" + uuid.uuid4().hex[:8])
    temp.mkdir(parents=True)
    for name, path in originals.items():
        shutil.copy2(path, temp / name)
        if db.file_sha(temp / name) != fingerprints[name]:
            raise ValueError("导入时原笔记发生变化：" + str(path))
    # Preserve the exact original HTML separately. Only its base URL is added to
    # the reading copy so existing image/timestamp/material links keep working.
    rendered = (temp / "original-notes.html").read_text()
    if re.search(r"<base\b", rendered, re.IGNORECASE):
        raise ValueError("原笔记含 base 标签，需要先核对链接：" + str(note))
    base = html.escape(relative_url(note.parent, folder) + "/", quote=True)
    rendered, count = re.subn(
        r"(<head\b[^>]*>)", lambda m: m[0] + f'<base href="{base}">', rendered, count=1, flags=re.IGNORECASE
    )
    if count != 1:
        raise ValueError("原笔记 HTML 缺少 head：" + str(note))
    (temp / "document.html").write_text(rendered)
    write_json(temp / "provenance.json", provenance)
    links = [("原始图文笔记", note.with_suffix(".html")), ("原始 Markdown", note), ("原学习目录", note.parent)]
    links += [("原视频：" + Path(s).name, Path(s)) for s in video.get("sources", [])]
    links += [
        ("原审阅／完成记录：" + n, p) for n, p in originals.items() if n not in ("notes.md", "original-notes.html")
    ]
    links += [("早期笔记（历史版本）", project / p) for p in video.get("previous_reviewed_notes", [])]
    rows = "".join(
        f'<li><a href="{html.escape(relative_url(p, folder), quote=True)}">{html.escape(label)}</a>'
        f"<br><small>{html.escape(str(p))}</small></li>"
        for label, p in links
    )
    (temp / "sources.html").write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        "<title>笔记与视频来源</title><style>body{max-width:900px;margin:40px auto;padding:0 24px;font:16px/1.8 system-ui;"
        "background:#f7f5f0;color:#252e29}a{color:#286a51}li{margin:16px 0;overflow-wrap:anywhere}</style>"
        f"<h1>{html.escape(video['title'])}</h1><p>{html.escape(batch['id'] + ' · ' + batch['label'])}</p>"
        f"<p>原审阅状态：{html.escape(video.get('review_status', 'unknown'))}</p>"
        "<p>原笔记与来源保持原位。此条目保存正文、图文笔记和审阅记录副本；图片、视频、详细查证及过程材料链接至原位置。</p>"
        f"<ul>{rows}</ul><p>视频登记指纹：<code>{html.escape(video.get('sha256', '未登记'))}</code></p>"
        '<p><a href="provenance.json">完整来源记录与原文指纹</a> · <a href="entry.json">本次收录文件清单</a></p></html>'
    )
    versions = [e for e in read_catalog(root)["entries"] if e["asset_id"] == video["asset_id"]]
    entry = {
        "entry_id": video["asset_id"] + "/" + key,
        "asset_id": video["asset_id"],
        "run_id": video.get("run_id", "legacy"),
        "kind": "historical_note",
        "title": video["title"],
        "source_sha256": video.get("sha256"),
        "document_version": 1 + max((e["document_version"] for e in versions), default=0),
        "created_at": time.time(),
        "source_completed_at": status.get("completed_at"),
        "directory": str(folder.relative_to(root)),
        "note_sha256": fingerprints["notes.md"],
        "knowledge_count": None,
        "review_status": video.get("review_status", "unknown"),
        "search_text": (temp / "notes.md").read_text(),
        "original_note_url": relative_url(note.with_suffix(".html"), root),
        "files": file_manifest(temp),
    }
    write_json(temp / "entry.json", entry)
    temp.rename(folder)
    return entry


def import_catalog(catalog_path, output, project=None, notes_directory=None):
    catalog_path = Path(catalog_path).resolve()
    project = Path(project).resolve() if project else catalog_path.parent.parent
    root = library_root(Path(output).resolve())
    scope = Path(notes_directory).resolve() if notes_directory else catalog_path.parent / "batches"
    if not scope.is_dir():
        raise ValueError("笔记输出目录不存在：" + str(scope))
    check_notes_directory(root, read_catalog(root), scope)
    prepared = prepare(catalog_path, project, scope)  # Only import notes inside this directory.
    if not prepared:
        raise ValueError("所选笔记输出目录中没有已登记的主笔记")
    existing = {b["id"]: b for b in read_catalog(root)["batches"]}
    for batch, _ in prepared:
        if batch["id"] in existing and existing[batch["id"]]["status"] != "historical_import":
            raise ValueError("原批次ID与流程批次冲突：" + batch["id"])
    if (root / "catalog.json").exists():
        backup = root / "import-backups" / (str(time.time_ns()) + ".json")
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / "catalog.json", backup)
    counts = {}
    for batch, videos in prepared:
        entries = [snapshot(root, project, catalog_path, batch, item) for item in videos]
        register_batch(
            output,
            {"id": batch["id"], "stage": "historical_import"},
            {"batch_label": batch["label"], "notes_directory": str(scope)},
            entries,
        )
        counts[batch["id"]] = len(entries)
    return {
        "index": str(root / "index.html"),
        "notes_directory": str(scope),
        "imported_courses": sum(counts.values()),
        "batches": counts,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--notes-directory", type=Path, help="仅收录这个笔记输出目录中的课程；默认目录文件旁的 batches")
    args = parser.parse_args()
    print(
        json.dumps(
            import_catalog(args.catalog, args.output, args.project, args.notes_directory), ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()

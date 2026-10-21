"""Immutable per-video evidence snapshots and an append-only, searchable batch library."""

from __future__ import annotations

import fcntl
import json
import re
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from . import db
from .config import atomic_write, data_dir
from .study import write_json

VERSION = 1


def library_root(output):
    return Path(output) / "learning-library"


def read_catalog(root):
    path = root / "catalog.json"
    return json.loads(path.read_text()) if path.exists() else {"schema_version": VERSION, "entries": [], "batches": []}


def _safe_name(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("资料记录ID包含不安全字符")
    return value


def file_manifest(folder):
    return [
        {"path": str(p.relative_to(folder)), "sha256": db.file_sha(p), "bytes": p.stat().st_size}
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p != folder / "entry.json"
    ]


def verify_entry(folder):
    entry = json.loads((folder / "entry.json").read_text())
    for record in entry["files"]:
        path = (folder / record["path"]).resolve()
        if folder.resolve() not in path.parents or not path.is_file() or db.file_sha(path) != record["sha256"]:
            raise ValueError("学习资料快照已变化：" + record["path"])
    return entry


def snapshot_run(output, run_id):
    from .exporting import export_document

    run = db.one("SELECT * FROM runs WHERE id=?", (run_id,))
    document = db.latest_document(run_id)
    if not run or not document or not run["status"].startswith("succeeded"):
        raise ValueError("仅可交接已完成且有笔记的视频")
    body = document["body"]
    asset = db.one("SELECT * FROM assets WHERE id=?", (run["asset_id"],))
    identity = _safe_name(run["asset_id"])
    key = f"{_safe_name(run_id)}-v{document['version']}-{db.digest(body)[:12]}"
    root = library_root(output)
    folder = root / "entries" / identity / key
    if (folder / "entry.json").exists():
        return verify_entry(folder)
    folder.parent.mkdir(parents=True, exist_ok=True)
    temp = folder.with_name(folder.name + ".partial-" + uuid.uuid4().hex[:8])
    temp.mkdir()
    # Export the complete note; do not reconstruct a summary or drop detailed records.
    export_document(run_id, body, temp, document["version"], register=False)
    shutil.copy2(temp / "document.md", temp / "notes.md")
    run_folder = data_dir() / "assets" / identity / "runs" / run_id
    if run_folder.exists():
        for path in run_folder.rglob("*"):
            if path.is_symlink():
                raise ValueError("过程材料包含符号链接，请先核对来源：" + str(path))
        shutil.copytree(run_folder, temp / "artifacts")
    # A run can reuse another run's checkpoints/frames; retain those dependencies too.
    units = db.rows("SELECT * FROM units WHERE run_id=?", (run_id,))
    artifacts = db.rows("SELECT * FROM artifacts WHERE run_id=?", (run_id,))
    dependencies = []
    for record, field in [(u, "artifact_path") for u in units] + [(a, "path") for a in artifacts]:
        original = (data_dir() / record[field]).resolve()
        if data_dir() not in original.parents or not original.is_file():
            raise ValueError("学习过程依赖文件缺失或越界：" + record[field])
        if run_folder.resolve() not in original.parents:
            target = temp / "reused-artifacts" / original.relative_to(data_dir())
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
            dependencies.append({"original": str(original), "retained": str(target.relative_to(temp))})
    write_json(
        temp / "database-records.json",
        {
            "run": run,
            "asset": asset,
            "units": units,
            "artifacts": artifacts,
            "sources": db.rows("SELECT * FROM sources WHERE asset_id=?", (identity,)),
            "documents": db.rows("SELECT * FROM documents WHERE run_id=?", (run_id,)),
            "events": db.rows("SELECT * FROM events WHERE run_id=?", (run_id,)),
            "reused_dependencies": dependencies,
        },
    )
    knowledge = []
    # Read original inventories once (exported quality/ may contain copies).
    for base in (temp / "artifacts/study", temp / "artifacts/note-quality"):
        for path in sorted(base.rglob("*-inventory.json")) if base.exists() else []:
            value = json.loads(path.read_text())
            relative = str(path.relative_to(temp))
            for item in value.get("items", []):
                knowledge.append(
                    {
                        **item,
                        "local_id": item["id"],
                        "id": "K-" + db.digest([identity, run_id, relative, item["id"]])[:20],
                        "inventory_path": relative,
                        "asset_id": identity,
                        "run_id": run_id,
                        "source_review": "generated_awaiting_source_review",
                    }
                )
    write_json(
        temp / "knowledge.json",
        {"items": knowledge, "status": "available" if knowledge else "missing_inventory_requires_review"},
    )
    quality = []
    for base in (temp / "artifacts/study", temp / "artifacts/note-quality"):
        for path in sorted(base.rglob("*-quality.json")) if base.exists() else []:
            value = json.loads(path.read_text())
            quality.append({"path": str(path.relative_to(temp)), "report": value})
    write_json(
        temp / "review-state.json",
        {
            "source_review": "generated_awaiting_source_review",
            "quality": quality,
            "warnings": body.get("warnings", []),
            "inventory_present": bool(knowledge),
        },
    )
    entry = {
        "entry_id": identity + "/" + key,
        "asset_id": identity,
        "run_id": run_id,
        "document_version": document["version"],
        "title": body.get("title") or asset["title"],
        "source_sha256": asset.get("sha256"),
        "created_at": time.time(),
        "directory": str(folder.relative_to(root)),
        "note_sha256": db.file_sha(temp / "notes.md"),
        "knowledge_count": len(knowledge),
        "review_status": "generated_awaiting_source_review",
        "search_text": (temp / "notes.md").read_text(),
        "files": file_manifest(temp),
    }
    write_json(temp / "entry.json", entry)
    # Different workers may have produced the same immutable snapshot. Never merge over it.
    if folder.exists():
        return verify_entry(folder)
    temp.rename(folder)
    return entry


def previous_notes(output, selected_ids=None):
    root = library_root(output)
    catalog = read_catalog(root)
    if selected_ids is not None:
        by_id = {e["entry_id"]: e for e in catalog["entries"]}
        if len(set(selected_ids)) != len(selected_ids) or any(i not in by_id for i in selected_ids):
            raise ValueError("预览中的历史笔记已不可用，请重新预览")
        return [entry_note(root, by_id[i]) for i in selected_ids]
    latest = {}
    for entry in sorted(catalog["entries"], key=lambda e: e["created_at"]):
        latest[entry["asset_id"]] = entry
    return [entry_note(root, e) for e in latest.values()]


def entry_note(root, entry):
    folder = root / entry["directory"]
    verify_entry(folder)
    path = folder / "notes.md"
    return {
        "path": str(path),
        "name": entry["title"],
        "chars": len(path.read_text()),
        "sha256": entry["note_sha256"],
        "source_identity": entry["asset_id"],
        "review_status": entry["review_status"],
        "entry_id": entry["entry_id"],
        "snapshot_directory": str(folder),
        "knowledge_path": str(folder / "knowledge.json") if (folder / "knowledge.json").is_file() else None,
        "title": entry["title"],
        "run_id": entry["run_id"],
    }


def register_batch(output, job, config, entries=(), result=None, error=None):
    root = library_root(output)
    root.mkdir(parents=True, exist_ok=True)
    ident = _safe_name(job["id"])
    with (root / ".catalog.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        catalog = read_catalog(root)
        records = {e["entry_id"]: e for e in catalog["entries"]}
        records.update({e["entry_id"]: e for e in entries})
        batches = {b["id"]: b for b in catalog["batches"]}
        batch = batches.get(
            ident,
            {
                "id": ident,
                "label": config.get("batch_label") or ident,
                "created_at": job.get("created_at", time.time()),
                "entry_ids": [],
            },
        )
        batch["entry_ids"] = list(dict.fromkeys(batch["entry_ids"] + [e["entry_id"] for e in entries]))
        batch["inherited_entry_ids"] = [n["entry_id"] for n in config.get("previous_notes", [])]
        batch["status"] = "failed" if error else ("complete" if result else job.get("stage", "learning"))
        if error:
            batch["error"] = str(error)
        else:
            batch.pop("error", None)
        if result:
            batch["result"] = result
        process = root / "batches" / ident / "process"
        batch["handoff_available"] = (process / "handoff-manifest.md").is_file()
        if process.exists():
            batch["process_files"] = file_manifest(process)
        batch["updated_at"] = time.time()
        batches[ident] = batch
        catalog.update(entries=list(records.values()), batches=list(batches.values()))
        write_json(root / "batches" / ident / "batch.json", batch)
        write_json(root / "catalog.json", catalog)
        render_index(root, catalog)
    return str(root / "index.html")


def render_index(root, catalog):
    display = {
        "entries": [],
        "batches": [
            {
                k: b[k]
                for k in (
                    "id",
                    "label",
                    "entry_ids",
                    "inherited_entry_ids",
                    "created_at",
                    "status",
                    "error",
                    "handoff_available",
                    "result",
                )
                if k in b
            }
            for b in catalog["batches"]
        ],
    }
    for entry in catalog["entries"]:
        base = quote(entry["directory"], safe="/")
        display["entries"].append(
            {
                k: entry[k]
                for k in [
                    "entry_id",
                    "asset_id",
                    "title",
                    "run_id",
                    "document_version",
                    "knowledge_count",
                    "review_status",
                    "search_text",
                    "created_at",
                ]
            }
            | {
                "note_url": base + "/document.html",
                "markdown_url": base + "/notes.md",
                "knowledge_url": base + "/knowledge.json" if entry.get("knowledge_count") is not None else None,
                "manifest_url": base + "/entry.json",
                "kind": entry.get("kind", "workflow_snapshot"),
                "source_url": base + "/sources.html" if entry.get("kind") == "historical_note" else None,
                "original_note_url": entry.get("original_note_url"),
            }
        )
    template = (Path(__file__).parent / "templates/learning-library.html").read_text()
    payload = json.dumps(display, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    atomic_write(root / "index.html", template.replace("__LIBRARY_DATA__", payload))


def collect_completed(job, config, items):
    entries = [
        snapshot_run(config["output_directory"], i["run_id"])
        for i in items
        if i.get("run_id") and (i.get("run_status") or "").startswith("succeeded")
    ]
    index = register_batch(config["output_directory"], job, config, entries)
    return [entry_note(library_root(config["output_directory"]), e) for e in entries], index


def freeze_handoff(job, config, notes):
    folder = data_dir() / "workflows" / job["id"]
    folder.mkdir(parents=True, exist_ok=True)
    snapshot = folder / "handoff.json"
    if snapshot.exists():
        notes = json.loads(snapshot.read_text())
    else:
        write_json(snapshot, notes)
    records = []
    for note in notes:
        if db.file_sha(Path(note["path"])) != note["sha256"]:
            raise ValueError("交接笔记已被修改，请创建新任务")
        entry = verify_entry(Path(note["snapshot_directory"])) if note.get("snapshot_directory") else None
        records.append(
            {
                "title": note.get("title", note["name"]),
                "note_path": note["path"],
                "sha256": note["sha256"],
                "source_identity": note["source_identity"],
                "source_sha256": entry.get("source_sha256") if entry else None,
                "run_id": note.get("run_id"),
                "document_version": entry["document_version"] if entry else None,
                "review_status": note["review_status"],
                "knowledge_path": note.get("knowledge_path"),
                "snapshot_directory": note.get("snapshot_directory"),
                "files": entry["files"] if entry else [],
                "knowledge_count": entry["knowledge_count"] if entry else None,
                "missing": [] if entry and entry["knowledge_count"] else ["knowledge_inventory"],
                "skill_input_policy": "notes_and_knowledge_text; images_preserved_not_reinspected",
            }
        )
    manifest = {
        "schema_version": VERSION,
        "workflow_id": job["id"],
        "batch_label": config.get("batch_label"),
        "config": config,
        "sources": records,
        "all_notes_retained": True,
        "source_review": "not_certified",
        "created_at": job.get("created_at", time.time()),
    }
    target = folder / "handoff-manifest.json"
    if target.exists() and json.loads(target.read_text()) != manifest:
        # Dynamic estimates are deliberately excluded by the caller's stable config below.
        old = json.loads(target.read_text())
        if old["sources"] != records:
            raise ValueError("交接文件清单变化，请创建新任务")
    else:
        write_json(target, manifest)
    rows = ["# 学习到 Skill 的交接清单", "", "笔记、截图及过程文件保留；图片未在 Skill 提炼时重新识别。", ""]
    for record in records:
        rows += [
            "## " + record["title"],
            f"- 笔记：{record['note_path']}",
            f"- 指纹：{record['sha256']}",
            f"- 审阅：{record['review_status']}",
            f"- 视频身份：{record['source_identity']} · 任务：{record['run_id']} · 文档版本：{record['document_version']}",
            f"- 视频指纹：{record['source_sha256']}",
            f"- 知识清单：{record['knowledge_path']}（{record['knowledge_count']} 项）",
            f"- 完整快照：{record['snapshot_directory']}",
            f"- 过程文件：{len(record['files'])} 个",
            "- 待补：" + ("、".join(record["missing"]) or "清单字段齐备，仍需独立来源审阅"),
            "",
        ]
        if record["snapshot_directory"]:
            names = {f["path"] for f in record["files"]}
            for title, name in [
                ("主笔记 HTML", "document.html"),
                ("详细查证", "detailed-records.md"),
                ("转写与时间戳", "transcript.srt"),
                ("检查与审阅状态", "review-state.json"),
                ("任务与来源记录", "database-records.json"),
            ]:
                rows.append(f"- [{'x' if name in names else ' '}] {title}：{name}")
            rows += [f"- 截图：{sum(name.startswith('images/') for name in names)} 张", ""]
    atomic_write(folder / "handoff-manifest.md", "\n".join(rows))
    return notes


def archive_workflow(job, config, result=None, error=None):
    """Retain all attempts even when generation fails; index remains usable."""
    root = library_root(config["output_directory"])
    source = data_dir() / "workflows" / job["id"]
    destination = root / "batches" / job["id"] / "process"
    if source.exists():
        shutil.copytree(source, destination, dirs_exist_ok=True)
    return register_batch(config["output_directory"], job, config, result=result, error=error)

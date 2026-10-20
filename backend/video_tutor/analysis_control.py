"""Local selection, estimates and durable per-job configuration."""

from __future__ import annotations
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field
from . import db
from .config import ROOT, Settings, data_dir, load_settings, save_settings
from .codex_client import codex_inventory
from .estimates import cost_multiplier, estimate
from .llm import model_inventory, verify_model
from .media import EXTENSIONS, probe


class StudyInput(BaseModel):
    quality_profile: Literal["legacy", "joker-ui-v2"] = "joker-ui-v2"
    paths: list[str] = Field(min_length=1, max_length=2000)
    output_directory: str = Field(min_length=1)
    provider: Literal["ollama", "codex"] = "codex"
    model: str = Field(default="gpt-6.1-sol", min_length=1, max_length=160)
    vision_model: str = Field(default="qwen3-vl:8b-instruct", max_length=160)
    text_model: str = Field(default="qwen3:14b", max_length=160)
    reasoning: str = Field(default="medium", max_length=30)
    service_tier: Literal["default", "priority"] = "default"
    request_id: str = Field(default="", max_length=100)


def provider_models(provider):
    if provider == "codex":
        result = codex_inventory()
        for model in result["models"]:
            model["cost_multiplier"] = cost_multiplier(model["model"])
            model["hour_estimate"] = estimate(3600000, "codex", model["model"])
        return result
    settings = load_settings()
    models = []
    for entry in model_inventory(settings):
        name = entry["name"]
        try:
            info = verify_model(settings, name)
            models.append(
                {
                    "model": name,
                    "displayName": name,
                    "inputModalities": ["text", "image"] if "vision" in info.get("capabilities", []) else ["text"],
                    "cost_multiplier": 0,
                    "supportedReasoningEfforts": [],
                }
            )
        except Exception as exc:
            # A cloud alias or one broken model must not hide the other installed models.
            models.append(
                {
                    "model": name,
                    "displayName": name,
                    "inputModalities": [],
                    "disabled": True,
                    "error": str(exc)[:200],
                    "cost_multiplier": None,
                }
            )
    return {"models": models, "logged_in": True, "source": "本机 Ollama · 已安装模型", "fetched_at": time.time()}


def native_pick(kind):
    if sys.platform != "darwin":
        raise ValueError("当前系统请直接填写文件或文件夹的绝对路径")
    if kind in ("files", "notes"):
        script = """set selected to choose file with prompt "选择输入文件，可按住 Command 多选" with multiple selections allowed
set paths to {}
repeat with f in selected
set end of paths to POSIX path of f
end repeat
set AppleScript's text item delimiters to linefeed
return paths as text"""
    else:
        script = (
            'return POSIX path of (choose folder with prompt "选择'
            + ("输出文件夹" if kind == "output" else "笔记文件夹" if kind == "notes_folder" else "视频文件夹")
            + '")'
        )
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script], check=False, text=True, capture_output=True, timeout=180
        )
    except subprocess.TimeoutExpired:
        raise ValueError("选择超时，请重试或直接输入路径")
    if result.returncode:
        if "(-128)" in result.stderr:
            return []
        raise ValueError("系统文件选择器未能打开，请直接填写路径")
    return [line for line in result.stdout.strip().splitlines() if line]


def output_path(value):
    if not value.strip():
        raise ValueError("请指定输出文件夹")
    path = Path(value).expanduser().resolve()
    if path == data_dir() or data_dir() in path.parents:
        raise ValueError("请使用应用数据目录之外的输出文件夹")
    if path.exists() and not path.is_dir():
        raise ValueError("输出路径必须是文件夹")
    parent = path
    while not parent.exists():
        parent = parent.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK):
        raise ValueError("输出文件夹不可写")
    return path


def selected_videos(paths, output):
    selected = {}
    for value in paths:
        if not value.strip():
            raise ValueError("输入路径不能为空")
        p = Path(value).expanduser().resolve()
        if not p.exists():
            raise ValueError("输入路径不存在：" + str(p))
        if p == data_dir() or data_dir() in p.parents:
            raise ValueError("不能将应用数据目录作为视频输入")
        candidates = p.rglob("*") if p.is_dir() else [p]
        for candidate in candidates:
            if candidate.is_symlink() or not candidate.is_file():
                continue
            f = candidate.resolve()
            if f.suffix.lower() not in EXTENSIONS:
                if p.is_file():
                    raise ValueError("不是支持的视频文件：" + p.name)
                continue
            if output == f or output in f.parents or data_dir() in f.parents:
                continue
            selected[str(f)] = f
            if len(selected) > 2000:
                raise ValueError("一次最多选择2000个视频，请分批添加")
    if not selected:
        raise ValueError("没有找到支持的视频，请检查输入与输出是否选择了同一目录")
    return sorted(selected.values())


def preview(body):
    output = output_path(body.output_directory)
    files, errors = [], []
    for path in selected_videos(body.paths, output):
        try:
            metadata = probe(path)
            duration = metadata["duration_ms"]
            stat = path.stat()
            files.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "duration_ms": duration,
                    "size_bytes": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
            )
        except Exception as exc:
            errors.append({"path": str(path), "error": str(exc)[:300]})
    duration = sum(f["duration_ms"] for f in files)
    return {
        "files": files,
        "errors": errors,
        "duration_ms": duration,
        "output_directory": str(output),
        "estimate": estimate(duration, body.provider, body.model, body.service_tier, body.reasoning),
    }


def validate_provider(body):
    catalog = provider_models(body.provider)
    if body.provider == "codex":
        if not catalog["logged_in"]:
            raise ValueError("请先在本机 Codex 客户端使用 ChatGPT 订阅登录；不使用 API 密钥")
        model = next((m for m in catalog["models"] if m["model"] == body.model), None)
        if not model:
            raise ValueError("所选模型已不在当前 Codex 可用列表，请刷新模型")
        if "image" not in model.get("inputModalities", ["text", "image"]):
            raise ValueError("视频学习需要支持图片输入的模型")
        efforts = [e["reasoningEffort"] for e in model.get("supportedReasoningEfforts", [])]
        if efforts and body.reasoning not in efforts:
            raise ValueError("该模型不支持所选思考档位，请重新选择")
        if body.service_tier != "default" and body.service_tier not in [s["id"] for s in model.get("serviceTiers", [])]:
            raise ValueError("该模型当前不支持所选速度")
    else:
        settings = load_settings()
        names = {m["model"] for m in catalog["models"] if not m.get("disabled")}
        if body.vision_model not in names or body.text_model not in names:
            raise ValueError("所选本地模型尚未安装，请刷新模型列表")
        verify_model(settings, body.vision_model, vision=True)
        verify_model(settings, body.text_model)
    return catalog


def enqueue(body):
    if body.request_id:
        previous = db.one("SELECT id FROM study_batches WHERE id=?", (body.request_id,))
        if previous:
            ensure_worker()
            return {"batch_id": previous["id"], "reused": True}
    validate_provider(body)
    result = preview(body)
    if result["errors"] or not result["files"]:
        raise ValueError("部分视频无法读取，请移除无效文件后再开始")
    settings = load_settings()
    config = settings.model_dump()
    config.update(
        learning_quality=body.quality_profile,
        analysis_provider=body.provider,
        codex_model=body.model,
        codex_reasoning=body.reasoning,
        codex_service_tier=body.service_tier,
        vision_model=body.vision_model,
        text_model=body.text_model,
        output_directory=result["output_directory"],
    )
    config["selected_files"] = [f["path"] for f in result["files"]]
    snapshot = Settings.model_validate(config)
    # Only these exact files are authorized; do not turn their parent directories into watched folders.
    settings.selected_files = list(dict.fromkeys(settings.selected_files + config["selected_files"]))
    save_settings(settings)
    batch_id = body.request_id or db.uid("batch_")
    with db.connect() as c:
        existing = c.execute("SELECT id FROM study_batches WHERE id=?", (batch_id,)).fetchone()
        if existing:
            return {"batch_id": batch_id, "reused": True}
        c.execute("INSERT INTO study_batches VALUES (?,?,?)", (batch_id, db.dumps(snapshot.model_dump()), time.time()))
        for f in result["files"]:
            c.execute(
                "INSERT INTO study_batch_items(id,batch_id,path) VALUES (?,?,?)", (db.uid("item_"), batch_id, f["path"])
            )
    ensure_worker()
    return {"batch_id": batch_id, "count": len(result["files"]), "estimate": result["estimate"]}


def process_study_batches(stop):
    from .worker import import_file

    items = db.rows(
        "SELECT i.*, b.config_json FROM study_batch_items i JOIN study_batches b ON i.batch_id=b.id WHERE i.status='queued' ORDER BY b.created_at,i.rowid"
    )
    for item in items:
        if stop.is_set():
            return
        try:
            workflow = db.one(
                "SELECT status FROM workflows WHERE batch_id=? OR id=?",
                (item["batch_id"], item["batch_id"].removeprefix("study-")),
            )
            if workflow and workflow["status"] in ("paused", "cancelled"):
                continue
            settings = Settings.model_validate_json(item["config_json"])
            db.execute("DELETE FROM ignored_sources WHERE path=?", (item["path"],))
            asset_id = import_file(item["path"], settings, auto_run=False, stable=False)
            if not asset_id:
                raise ValueError("视频未能导入，请重新选择")
            # Atomic run creation + item association prevents duplicate billing after worker restart.
            with db.connect() as c:
                current = c.execute("SELECT status FROM study_batch_items WHERE id=?", (item["id"],)).fetchone()
                if current["status"] != "queued":
                    continue
                run_id = db.uid("r_")
                c.execute(
                    "INSERT INTO runs(id,asset_id,status,config_json,options_json,created_at) VALUES (?,?,?,?,?,?)",
                    (run_id, asset_id, "queued", item["config_json"], "{}", time.time()),
                )
                c.execute("UPDATE study_batch_items SET status='imported',run_id=? WHERE id=?", (run_id, item["id"]))
            db.event(run_id, "queued", "已按新建学习配置加入队列")
        except Exception as exc:
            db.execute("UPDATE study_batch_items SET status='failed',error=? WHERE id=?", (str(exc)[:1000], item["id"]))


def ensure_worker():
    """Wake the local queue after an explicit start; the worker holds its own singleton lock."""
    heartbeat = db.one("SELECT value FROM runtime WHERE key='worker_heartbeat'")
    if heartbeat and time.time() - float(heartbeat["value"]) < 35:
        return
    env = os.environ.copy()
    env["VIDEO_TUTOR_DATA"] = str(data_dir())
    env["PYTHONPATH"] = str(ROOT / "backend")
    logs = data_dir() / "logs"
    logs.mkdir(exist_ok=True)
    with (logs / "worker.log").open("a") as log:
        subprocess.Popen(
            [sys.executable, "-m", "video_tutor", "worker"],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )

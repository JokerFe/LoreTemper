from __future__ import annotations
import contextlib
import hmac
import json
import mimetypes
import shutil
import time
from pathlib import Path
from typing import Literal
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import db
from .workflows import WorkflowInput
from .analysis_control import StudyInput, enqueue, native_pick, preview, provider_models
from .config import ROOT, Settings, data_dir, load_settings, local_token, save_settings
from .exporting import export_document
from .llm import model_inventory
from .media import binary
from .retrieval import search
from .worker import is_allowed


@contextlib.asynccontextmanager
async def lifespan(app):
    db.init_db()
    local_token()
    if db.one("SELECT id FROM workflows WHERE status IN ('queued','learning','running') LIMIT 1"):
        from .workflows import ensure_workflow_worker
        ensure_workflow_worker()
    yield


app = FastAPI(title="视频学习工作台", lifespan=lifespan)


@app.middleware("http")
async def protect(request: Request, call_next):
    host = request.url.hostname
    if host not in ("127.0.0.1", "localhost", "::1", "testserver"):
        return JSONResponse({"message": "Host 不被允许"}, 400)
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        return JSONResponse({"message": "拒绝跨站请求"}, 403)
    protected = (
        request.url.path.startswith(("/api/", "/media/", "/artifacts/")) and request.url.path != "/api/v1/session"
    )
    if protected:
        token = request.cookies.get("vt_session", "")
        if not hmac.compare_digest(token, local_token()):
            return JSONResponse({"message": "请通过项目启动器打开工作台"}, 401)
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-vt-request") != "1":
            return JSONResponse({"message": "缺少请求校验"}, 403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
    )
    return response


@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse({"message": str(exc), "code": "invalid_input"}, 400)


class SessionInput(BaseModel):
    token: str


@app.post("/api/v1/session")
def session(body: SessionInput):
    if not hmac.compare_digest(body.token, local_token()):
        raise HTTPException(401, "打开凭据无效")
    response = JSONResponse({"ok": True})
    response.set_cookie("vt_session", local_token(), httponly=True, samesite="strict", max_age=30 * 86400)
    return response


@app.get("/api/v1/health")
def health():
    heartbeat = db.one("SELECT value FROM runtime WHERE key=?", ("worker_heartbeat",))
    reason = db.one("SELECT value FROM runtime WHERE key=?", ("waiting_reason",))
    return {
        "ok": True,
        "worker_alive": bool(heartbeat and time.time() - float(heartbeat["value"]) < 35),
        "waiting_reason": reason["value"] if reason else "",
    }


@app.get("/api/v1/doctor")
def doctor():
    settings = load_settings()
    checks = []
    for name in ("ffmpeg", "ffprobe", "whisper-cli"):
        try:
            checks.append({"name": name, "ok": True, "detail": binary(name)})
        except RuntimeError as e:
            checks.append({"name": name, "ok": False, "detail": str(e)})
    checks.append(
        {
            "name": "Whisper 模型",
            "ok": Path(settings.whisper_model_path).is_file(),
            "detail": settings.whisper_model_path,
        }
    )
    try:
        models = model_inventory(settings)
        names = {m["name"] for m in models}
        checks += [
            {"name": n, "ok": n in names, "detail": "已安装" if n in names else "尚未下载"}
            for n in (settings.vision_model, settings.text_model)
        ]
    except httpx.HTTPError:
        checks.append({"name": "Ollama", "ok": False, "detail": "本地服务未启动"})
    free = shutil.disk_usage(data_dir()).free / 1e9
    checks.append({"name": "磁盘空间", "ok": free >= settings.min_free_disk_gb, "detail": f"剩余 {free:.1f} GB"})
    return {"checks": checks, "ready": all(c["ok"] for c in checks), "data_dir": str(data_dir())}


@app.get("/api/v1/settings")
def settings_get():
    return load_settings().model_dump()


@app.put("/api/v1/settings")
def settings_put(body: Settings):
    for p in body.directories:
        if not Path(p).is_dir():
            raise ValueError("目录不存在：" + p)
        if Path(p) == data_dir() or data_dir() in Path(p).parents:
            raise ValueError("不能监控应用数据目录")
    save_settings(body)
    return body.model_dump()


class ImportInput(BaseModel):
    path: str


@app.post("/api/v1/imports", status_code=202)
def imports(body: ImportInput):
    settings = load_settings()
    p = Path(body.path).expanduser().resolve()
    if not is_allowed(p, settings):
        raise ValueError("请先在设置中添加该文件所在的视频目录")
    if not p.exists():
        raise ValueError("文件或目录不存在")
    i = db.uid("i_")
    db.execute("INSERT INTO imports VALUES (?,?,?,?,?,?)", (i, str(p), "queued", None, None, time.time()))
    return {"import_id": i, "status": "queued"}


@app.get("/api/v1/imports")
def import_list():
    return db.rows("SELECT * FROM imports ORDER BY created_at DESC LIMIT 20")


@app.get("/api/v1/assets")
def assets():
    return db.asset_list()


@app.get("/api/v1/assets/{asset_id}")
def asset(asset_id: str):
    row = db.one("SELECT * FROM assets WHERE id=?", (asset_id,))
    if not row:
        raise HTTPException(404, "视频不存在")
    row["runs"] = db.rows("SELECT * FROM runs WHERE asset_id=? ORDER BY created_at DESC", (asset_id,))
    row["sources"] = db.rows("SELECT path FROM sources WHERE asset_id=?", (asset_id,))
    return row


class RunInput(BaseModel):
    profile: str | None = None
    visual_analysis: Literal["full", "keyframes"] | None = None
    start_ms: int | None = Field(None, ge=0)
    end_ms: int | None = Field(None, ge=0)


@app.post("/api/v1/assets/{asset_id}/runs", status_code=202)
def new_run(asset_id: str, body: RunInput):
    s = load_settings()
    if body.profile:
        s = Settings.model_validate({**s.model_dump(), "profile": body.profile})
    options = body.model_dump(exclude_none=True)
    options.pop("profile", None)
    if "start_ms" in options and "end_ms" in options and options["start_ms"] >= options["end_ms"]:
        raise ValueError("结束时间必须晚于开始时间")
    return {"run_id": db.create_run(asset_id, s, options)}


@app.get("/api/v1/runs/{run_id}")
def get_run(run_id: str):
    r = db.one("SELECT * FROM runs WHERE id=?", (run_id,))
    if not r:
        raise HTTPException(404, "任务不存在")
    r["exports"] = db.rows("SELECT id,path FROM artifacts WHERE run_id=? AND kind='export'", (run_id,))
    return r


@app.post("/api/v1/runs/{run_id}/{action}")
def control(run_id: str, action: str):
    if action not in ("pause", "resume", "cancel", "retry"):
        raise HTTPException(404)
    return {"status": db.control(run_id, action)}


@app.get("/api/v1/runs/{run_id}/events")
def events(run_id: str, after_seq: int = 0):
    return db.rows("SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT 300", (run_id, after_seq))


@app.get("/api/v1/runs/{run_id}/documents/latest")
def doc(run_id: str):
    d = db.latest_document(run_id)
    if not d:
        raise HTTPException(404, "笔记尚未生成")
    if d['body'].get('summary_markdown'):
        from .codex_pipeline import render_notes
        d['body']['notes_html'] = render_notes(d['body']['summary_markdown'], d['body']['frames'])
    return d


@app.get("/api/v1/runs/{run_id}/transcript")
def transcript_get(run_id: str):
    document = db.latest_document(run_id)
    if document:
        return document["body"]["transcript"]
    units = db.rows(
        "SELECT artifact_path FROM units WHERE run_id=? AND stage='transcribe' ORDER BY unit_key", (run_id,)
    )
    from .asr import merge_segments

    segments = []
    for unit in units:
        path = data_dir() / unit["artifact_path"]
        if path.is_file():
            segments = merge_segments(segments, json.loads(path.read_text()))
    return segments


@app.get("/api/v1/runs/{run_id}/steps")
def steps_get(run_id: str):
    document = db.latest_document(run_id)
    return [step for chapter in document["body"]["chapters"] for step in chapter["steps"]] if document else []


class EditInput(BaseModel):
    base_version: int
    user_notes: str = Field(max_length=50000)


@app.post("/api/v1/runs/{run_id}/documents/edit")
def edit(run_id: str, body: EditInput):
    d = db.latest_document(run_id)
    if not d:
        raise HTTPException(404)
    d["body"]["user_notes"] = body.user_notes
    try:
        v = db.save_document(run_id, d["body"], "user", body.base_version)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"version": v}


@app.post("/api/v1/runs/{run_id}/exports/create")
def export(run_id: str):
    d = db.latest_document(run_id)
    if not d:
        raise HTTPException(404)
    folder = data_dir() / "assets" / d["body"]["asset_id"] / "runs" / run_id / "exports" / f"v{d['version']}"
    return export_document(run_id, d["body"], folder, d["version"])


@app.get("/api/v1/search")
def find(q: str):
    return search(q)


@app.get("/artifacts/{artifact_id}")
def artifact(artifact_id: str):
    row = db.one("SELECT * FROM artifacts WHERE id=?", (artifact_id,))
    if not row:
        raise HTTPException(404)
    p = (data_dir() / row["path"]).resolve()
    if data_dir() not in p.parents or not p.is_file():
        raise HTTPException(404)
    if row["kind"] == "export":
        return FileResponse(p, filename=p.name)
    return FileResponse(p)


def range_response(path, range_header):
    size = path.stat().st_size
    if not range_header:
        return FileResponse(path, headers={"Accept-Ranges": "bytes"})
    import re

    m = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
    if not m or (not m[1] and not m[2]):
        raise HTTPException(416, headers={"Content-Range": f"bytes */{size}"})
    if not m[1]:
        a = max(0, size - int(m[2]))
        b = size - 1
    else:
        a = int(m[1])
        b = min(size - 1, int(m[2]) if m[2] else size - 1)
    if a >= size or a > b:
        raise HTTPException(416, headers={"Content-Range": f"bytes */{size}"})

    def generate():
        with path.open("rb") as f:
            f.seek(a)
            left = b - a + 1
            while left:
                chunk = f.read(min(left, 1024 * 1024))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    return StreamingResponse(
        generate(),
        206,
        media_type=mimetypes.guess_type(path)[0] or "video/mp4",
        headers={"Content-Range": f"bytes {a}-{b}/{size}", "Content-Length": str(b - a + 1), "Accept-Ranges": "bytes"},
    )


@app.get("/media/assets/{asset_id}")
def video(asset_id: str, request: Request):
    proxy = db.one(
        "SELECT a.path FROM artifacts a JOIN runs r ON a.run_id=r.id WHERE r.asset_id=? AND a.kind='proxy' ORDER BY r.created_at DESC LIMIT 1",
        (asset_id,),
    )
    if proxy and (data_dir() / proxy["path"]).is_file():
        return range_response(data_dir() / proxy["path"], request.headers.get("range"))
    sources = db.rows("SELECT path FROM sources WHERE asset_id=?", (asset_id,))
    source = next((Path(s["path"]) for s in sources if Path(s["path"]).is_file()), None)
    if not source:
        raise HTTPException(404, "原视频不可用")
    if not is_allowed(source, load_settings()):
        raise HTTPException(403, "该目录已不在允许范围")
    return range_response(source, request.headers.get("range"))


@app.delete("/api/v1/assets/{asset_id}")
def delete_asset(asset_id: str):
    active = db.one(
        "SELECT id FROM runs WHERE asset_id=? AND status IN ('running','queued','pause_requested','cancel_requested')",
        (asset_id,),
    )
    if active:
        raise ValueError("请先取消该视频的活动任务")
    with db.connect() as c:
        for x in c.execute("SELECT id FROM search_chunks WHERE asset_id=?", (asset_id,)).fetchall():
            c.execute("DELETE FROM search_fts WHERE rowid=?", (x[0],))
        c.execute(
            "INSERT OR REPLACE INTO ignored_sources SELECT path,stat_json FROM sources WHERE asset_id=?", (asset_id,)
        )
        c.execute("DELETE FROM assets WHERE id=?", (asset_id,))
    target = data_dir() / "assets" / asset_id
    if target.parent == data_dir() / "assets":
        shutil.rmtree(target, ignore_errors=True)
    return {"ok": True, "source_deleted": False}


# Analysis controls share the same local-session and CSRF protections as the library.


@app.get("/api/v1/analysis/models")
def analysis_models(provider: Literal["codex", "ollama"]):
    try:
        return provider_models(provider)
    except (RuntimeError, OSError, httpx.HTTPError) as exc:
        raise HTTPException(503, str(exc)[:400])


@app.get("/api/v1/analysis/defaults")
def analysis_defaults():
    s = load_settings()
    return {"provider": "codex", "model": s.codex_model, "reasoning": s.codex_reasoning,
            "service_tier": s.codex_service_tier, "vision_model": s.vision_model, "text_model": s.text_model,
            "output_directory": s.output_directory or str(ROOT / "learning" / "exports")}


class PickerInput(BaseModel):
    kind: Literal["files", "folder", "output", "notes", "notes_folder"]


@app.post("/api/v1/analysis/pick")
def analysis_pick(body: PickerInput):
    return {"paths": native_pick(body.kind)}


@app.post("/api/v1/analysis/preview")
def analysis_preview(body: StudyInput):
    return preview(body)


@app.post("/api/v1/analysis/jobs", status_code=202)
def analysis_enqueue(body: StudyInput):
    try:
        return enqueue(body)
    except (RuntimeError, OSError, httpx.HTTPError) as exc:
        raise HTTPException(503, str(exc)[:400])


@app.get("/api/v1/analysis/jobs")
def analysis_jobs():
    batches = db.rows("SELECT * FROM study_batches ORDER BY created_at DESC LIMIT 20")
    for batch in batches:
        config = Settings.model_validate_json(batch.pop('config_json'))
        batch['provider'] = config.analysis_provider
        batch['model'] = config.codex_model if config.analysis_provider == 'codex' else config.text_model
        batch['reasoning'] = config.codex_reasoning
        batch['output_directory'] = config.output_directory
        batch['items'] = db.rows("SELECT i.*,r.status AS run_status,r.stage,r.progress_done,r.progress_total,r.error AS run_error FROM study_batch_items i LEFT JOIN runs r ON r.id=i.run_id WHERE batch_id=? ORDER BY i.rowid", (batch['id'],))
    return batches


@app.get("/api/v1/runs/{run_id}/usage")
def run_usage(run_id: str):
    run = db.one("SELECT * FROM runs WHERE id=?", (run_id,))
    if not run:
        raise HTTPException(404, '任务不存在')
    config = Settings.model_validate_json(run['config_json'])
    folder = data_dir() / 'assets' / run['asset_id'] / 'runs' / run_id
    from .estimates import equivalent_cost
    if config.analysis_provider == 'codex':
        usage, missing = {}, 0
        for path in folder.glob('study/**/calls/*/attempt-*.json'):
            record = json.loads(path.read_text())
            if not record.get('usage'):
                missing += 1
            for key, value in record.get('usage', {}).items():
                if isinstance(value, (int, float)):
                    usage[key] = usage.get(key, 0) + value
        return {'provider': 'codex', 'model': config.codex_model, 'usage': usage,
                'unmetered_attempts': missing, 'api_equivalent_usd': equivalent_cost(usage, config.codex_model, config.codex_service_tier),
                'caveat': '调用回执累计；输入含缓存、输出含推理，非订阅账单；进行中的调用尚未计入。'}
    receipt = folder / 'metrics.json'
    metrics = json.loads(receipt.read_text()) if receipt.exists() else None
    usage = None
    if metrics:
        usage = {'input_tokens': sum(r.get('prompt_eval_count') or 0 for r in metrics.get('requests', [])),
                 'output_tokens': sum(r.get('eval_count') or 0 for r in metrics.get('requests', [])), 'cached_input_tokens': 0}
    return {'provider': 'ollama', 'api_equivalent_usd': 0, 'usage': usage, 'metrics': metrics}




@app.get("/api/v1/workflows/defaults")
def workflow_defaults():
    from .workflows import default_inputs
    return default_inputs()


@app.post("/api/v1/workflows/resolve")
def workflow_resolve(body: WorkflowInput):
    from .workflows import resolve
    return resolve(body)


@app.post("/api/v1/workflows/jobs", status_code=202)
def workflow_submit(body: WorkflowInput):
    from .workflows import submit
    return submit(body)


@app.get("/api/v1/workflows/jobs")
def workflow_jobs():
    from .workflows import jobs
    return jobs()


@app.post("/api/v1/workflows/jobs/{ident}/{action}")
def workflow_control(ident: str, action: str):
    from .workflows import control
    return control(ident, action)


static = ROOT / "frontend" / "dist"
if static.exists():
    app.mount("/", StaticFiles(directory=static, html=True), name="ui")
else:

    @app.get("/")
    def missing_ui():
        return {"message": "请先在 frontend 中运行 npm install 和 npm run build"}

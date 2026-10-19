from __future__ import annotations
import contextlib
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from .config import data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, title TEXT NOT NULL, size_bytes INTEGER NOT NULL, media_json TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sources(path TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE, stat_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE, status TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'queued', progress_done INTEGER NOT NULL DEFAULT 0, progress_total INTEGER NOT NULL DEFAULT 0, config_json TEXT NOT NULL, options_json TEXT NOT NULL DEFAULT '{}', warnings_json TEXT NOT NULL DEFAULT '[]', error TEXT, generation INTEGER NOT NULL DEFAULT 0, lease_owner TEXT, lease_expires REAL, started_at REAL, finished_at REAL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS runs_asset_time ON runs(asset_id,created_at);
CREATE TABLE IF NOT EXISTS units(run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, stage TEXT NOT NULL, unit_key TEXT NOT NULL, input_hash TEXT NOT NULL, artifact_path TEXT NOT NULL, artifact_sha TEXT NOT NULL, duration REAL NOT NULL, PRIMARY KEY(run_id,stage,unit_key));
CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, kind TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, UNIQUE(run_id,path));
CREATE TABLE IF NOT EXISTS documents(run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, version INTEGER NOT NULL, origin TEXT NOT NULL, body_json TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(run_id,version));
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT REFERENCES runs(id) ON DELETE CASCADE, level TEXT NOT NULL, code TEXT NOT NULL, message TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS search_chunks(id INTEGER PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, kind TEXT NOT NULL, start_ms INTEGER NOT NULL, raw_text TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(search_text);
CREATE TABLE IF NOT EXISTS ignored_sources(path TEXT PRIMARY KEY, stat_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY,path TEXT NOT NULL,status TEXT NOT NULL,result_json TEXT,error TEXT,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runtime(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS study_batches(id TEXT PRIMARY KEY, config_json TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS study_batch_items(id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES study_batches(id),path TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',run_id TEXT,error TEXT);
CREATE TABLE IF NOT EXISTS workflows(id TEXT PRIMARY KEY,mode TEXT NOT NULL,status TEXT NOT NULL,stage TEXT NOT NULL,config_json TEXT NOT NULL,batch_id TEXT,result_json TEXT,error TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL);
PRAGMA user_version=1;
"""


def connect():
    c = sqlite3.connect(data_dir() / "app.sqlite3", timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=10000")
    return c


def init_db():
    with connect() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)


def rows(sql, args=()):
    with contextlib.closing(connect()) as c:
        return [dict(x) for x in c.execute(sql, args).fetchall()]


def one(sql, args=()):
    out = rows(sql, args)
    return out[0] if out else None


def execute(sql, args=()):
    with contextlib.closing(connect()) as c, c:
        return c.execute(sql, args).rowcount


def uid(prefix=""):
    return prefix + uuid.uuid4().hex[:16]


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def file_sha(p: Path):
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def event(run_id, code, message, level="info"):
    execute(
        "INSERT INTO events(run_id,level,code,message,created_at) VALUES (?,?,?,?,?)",
        (run_id, level, code, message, time.time()),
    )


def create_run(asset_id, settings, options=None):
    if not one("SELECT id FROM assets WHERE id=?", (asset_id,)):
        raise ValueError("视频不存在")
    run_id = uid("r_")
    execute(
        "INSERT INTO runs(id,asset_id,status,config_json,options_json,created_at) VALUES (?,?,?,?,?,?)",
        (run_id, asset_id, "queued", dumps(settings.model_dump()), dumps(options or {}), time.time()),
    )
    event(run_id, "queued", "已加入处理队列")
    return run_id


def control(run_id, action):
    with contextlib.closing(connect()) as c, c:
        c.execute("BEGIN IMMEDIATE")
        r = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not r:
            raise ValueError("任务不存在")
        status = r["status"]
        target = None
        if action == "pause":
            target = "paused" if status == "queued" else "pause_requested" if status == "running" else None
        elif action == "cancel":
            target = (
                "cancel_requested"
                if status in ("running", "pause_requested")
                else "cancelled"
                if status in ("queued", "paused", "failed")
                else None
            )
        elif action in ("resume", "retry") and status in ("paused", "failed", "cancelled"):
            target = "queued"
        if target is None:
            raise ValueError("当前状态不支持该操作")
        c.execute("UPDATE runs SET status=?,error=NULL WHERE id=?", (target, run_id))
    event(
        run_id,
        action,
        {"pause": "正在暂停", "cancel": "正在取消", "resume": "已恢复队列", "retry": "已安排重试"}[action],
    )
    return target


def save_document(run_id, body, origin="model", base_version=None):
    with contextlib.closing(connect()) as c, c:
        c.execute("BEGIN IMMEDIATE")
        latest = c.execute("SELECT MAX(version) FROM documents WHERE run_id=?", (run_id,)).fetchone()[0] or 0
        if base_version is not None and base_version != latest:
            raise ValueError("文档已更新，请刷新后再保存")
        c.execute("INSERT INTO documents VALUES (?,?,?,?,?)", (run_id, latest + 1, origin, dumps(body), time.time()))
        return latest + 1


def latest_document(run_id):
    row = one("SELECT * FROM documents WHERE run_id=? ORDER BY version DESC LIMIT 1", (run_id,))
    if row:
        row["body"] = json.loads(row.pop("body_json"))
    return row


def asset_list():
    return rows("""SELECT a.*, (SELECT path FROM sources s WHERE s.asset_id=a.id LIMIT 1) AS path,
    r.id AS run_id,r.status,r.stage,r.progress_done,r.progress_total,r.error,r.warnings_json
    FROM assets a LEFT JOIN runs r ON r.id=(SELECT id FROM runs WHERE asset_id=a.id ORDER BY created_at DESC LIMIT 1)
    ORDER BY a.created_at DESC""")


def add_artifact(run_id, p, kind):
    relative = str(p.resolve().relative_to(data_dir()))
    existing = one("SELECT id FROM artifacts WHERE run_id=? AND path=?", (run_id, relative))
    aid = existing["id"] if existing else uid("a_")
    execute(
        "INSERT INTO artifacts(id,run_id,kind,path,sha256) VALUES (?,?,?,?,?) ON CONFLICT(run_id,path) DO UPDATE SET sha256=excluded.sha256",
        (aid, run_id, kind, relative, file_sha(p)),
    )
    return aid

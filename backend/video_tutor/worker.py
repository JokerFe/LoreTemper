from __future__ import annotations
import fcntl
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from . import db
from .config import data_dir, load_settings
from .media import EXTENSIONS, probe
from .pipeline import Pipeline, StopWork


def is_allowed(path, settings):
    p = Path(path).expanduser().resolve()
    if p == data_dir() or data_dir() in p.parents:
        return False
    if str(p) in settings.selected_files:
        return True
    return any(p == Path(root) or Path(root) in p.parents for root in settings.directories)


def file_stat(p):
    s = p.stat()
    return {"size": s.st_size, "mtime_ns": s.st_mtime_ns}


def import_file(path, settings, auto_run=True, stable=True):
    p = Path(path).expanduser().resolve()
    if not is_allowed(p, settings):
        raise ValueError("路径不在允许的视频目录内")
    if not p.is_file() or p.suffix.lower() not in EXTENSIONS:
        raise ValueError("不是支持的视频文件")
    stat = file_stat(p)
    ignored = db.one("SELECT stat_json FROM ignored_sources WHERE path=?", (str(p),))
    if ignored and json.loads(ignored["stat_json"]) == stat:
        return None
    if stable and time.time() - p.stat().st_mtime < settings.stable_seconds:
        return None
    old = db.one("SELECT * FROM sources WHERE path=?", (str(p),))
    if old and json.loads(old["stat_json"]) == stat:
        return old["asset_id"]
    sha = db.file_sha(p)
    if file_stat(p) != stat:
        return None
    metadata = probe(p)
    if file_stat(p) != stat:
        return None
    existing = db.one("SELECT id FROM assets WHERE sha256=?", (sha,))
    aid = existing["id"] if existing else db.uid("v_")
    with db.connect() as c:
        c.execute(
            "INSERT OR IGNORE INTO assets VALUES (?,?,?,?,?,?)",
            (aid, sha, p.stem, stat["size"], db.dumps(metadata), time.time()),
        )
        c.execute(
            "INSERT INTO sources VALUES (?,?,?) ON CONFLICT(path) DO UPDATE SET asset_id=excluded.asset_id,stat_json=excluded.stat_json",
            (str(p), aid, db.dumps(stat)),
        )
    if not existing and auto_run:
        db.create_run(aid, settings)
    return aid


def import_path(path, settings):
    p = Path(path).expanduser().resolve()
    if not is_allowed(p, settings):
        raise ValueError("请先将该文件的上级目录加入允许目录")
    files = (
        [p]
        if p.is_file()
        else (x for x in p.rglob("*") if x.is_file() and not x.is_symlink() and x.suffix.lower() in EXTENSIONS)
    )
    out = []
    for f in files:
        if is_allowed(f, settings):
            try:
                db.execute("DELETE FROM ignored_sources WHERE path=?", (str(f.resolve()),))
                aid = import_file(f, settings)
                if aid:
                    out.append(aid)
            except Exception as e:
                db.event(None, "import_error", f.name + ": " + str(e)[:300], "warning")
    return out


def on_ac_power():
    if os.uname().sysname != "Darwin":
        return True
    try:
        return "AC Power" in subprocess.check_output(["/usr/bin/pmset", "-g", "batt"], timeout=5, text=True)
    except (OSError, subprocess.SubprocessError):
        return True


def claim(owner):
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        # Single-instance lock guarantees expired work cannot be executed by a second local worker.
        c.execute(
            "UPDATE runs SET status=CASE WHEN status='pause_requested' THEN 'paused' WHEN status='cancel_requested' THEN 'cancelled' ELSE 'queued' END,lease_owner=NULL WHERE status IN ('running','pause_requested','cancel_requested') AND (lease_expires IS NULL OR lease_expires<?)",
            (time.time(),),
        )
        row = c.execute("SELECT * FROM runs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if not row:
            return None
        c.execute(
            "UPDATE runs SET status='running',generation=generation+1,lease_owner=?,lease_expires=?,started_at=COALESCE(started_at,?),error=NULL WHERE id=?",
            (owner, time.time() + 90, time.time(), row["id"]),
        )
        return dict(c.execute("SELECT * FROM runs WHERE id=?", (row["id"],)).fetchone())


class Changed(FileSystemEventHandler):
    def __init__(self, wake):
        self.wake = wake

    def on_any_event(self, event):
        if not event.is_directory:
            self.wake.set()


def scanner(stop):
    wake = threading.Event()
    observer = None
    watched = None
    stable_seen = {}
    while not stop.is_set():
        try:
            settings = load_settings()
            if watched != settings.directories:
                if observer:
                    observer.stop()
                    observer.join(timeout=3)
                observer = Observer()
                watched = list(settings.directories)
                for root in watched:
                    if Path(root).is_dir():
                        observer.schedule(Changed(wake), root, recursive=True)
                observer.start()
            from .analysis_control import process_study_batches

            process_study_batches(stop)
            jobs = db.rows("SELECT * FROM imports WHERE status='queued'")
            for job in jobs:
                db.execute("UPDATE imports SET status='running' WHERE id=?", (job["id"],))
                try:
                    result = import_path(job["path"], settings)
                    db.execute(
                        "UPDATE imports SET status='succeeded',result_json=? WHERE id=?", (db.dumps(result), job["id"])
                    )
                except Exception as e:
                    db.execute("UPDATE imports SET status='failed',error=? WHERE id=?", (str(e), job["id"]))
            if settings.auto_import:
                for root in settings.directories:
                    p = Path(root)
                    if not p.is_dir():
                        continue
                    for f in p.rglob("*"):
                        if stop.is_set():
                            return
                        if (
                            not f.is_file()
                            or f.is_symlink()
                            or f.suffix.lower() not in EXTENSIONS
                            or not is_allowed(f, settings)
                        ):
                            continue
                        stat = file_stat(f)
                        old = stable_seen.get(str(f))
                        if old and old[0] == stat and time.time() - old[1] >= settings.stable_seconds:
                            try:
                                import_file(f, settings)
                            except Exception as e:
                                db.event(None, "scan_error", f.name + ": " + str(e)[:300], "warning")
                        else:
                            if not old or old[0] != stat:
                                stable_seen[str(f)] = (stat, time.time())
            wake.wait(settings.scan_seconds)
            wake.clear()
        except Exception as e:
            db.event(None, "scanner_error", str(e)[:300], "warning")
            stop.wait(10)
    if observer:
        observer.stop()
        observer.join(timeout=3)


def run_worker(stop=None):
    db.init_db()
    stop = stop or threading.Event()
    lock = (data_dir() / "worker.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("已经有一个任务进程在运行")
    owner = db.uid("worker_")
    active = [None]
    # This process owns the global lock, therefore prior leases are stale even before their timeout.
    db.execute("UPDATE runs SET lease_expires=0 WHERE status IN ('running','pause_requested','cancel_requested')")
    db.execute("UPDATE imports SET status='queued' WHERE status='running'")

    def heartbeat():
        while not stop.is_set():
            db.execute("INSERT OR REPLACE INTO runtime VALUES (?,?)", ("worker_heartbeat", str(time.time())))
            if active[0]:
                db.execute(
                    "UPDATE runs SET lease_expires=? WHERE id=? AND lease_owner=?", (time.time() + 90, active[0], owner)
                )
            stop.wait(10)

    hb = threading.Thread(target=heartbeat, daemon=True)
    hb.start()
    scan = threading.Thread(target=scanner, args=(stop,), daemon=True)
    scan.start()
    try:
        while not stop.is_set():
            settings = load_settings()
            reason = ""
            if settings.require_ac_power and not on_ac_power():
                reason = "等待连接电源"
            if shutil.disk_usage(data_dir()).free < settings.min_free_disk_gb * 1e9:
                reason = "磁盘空间不足"
            db.execute("INSERT OR REPLACE INTO runtime VALUES (?,?)", ("waiting_reason", reason))
            if reason:
                stop.wait(3)
                continue
            run = claim(owner)
            if not run:
                stop.wait(1)
                continue
            active[0] = run["id"]
            try:
                Pipeline(run, owner).execute()
            except StopWork:
                pass
            except Exception as e:
                db.execute(
                    "UPDATE runs SET status='failed',error=?,finished_at=?,lease_owner=NULL,lease_expires=NULL WHERE id=? AND lease_owner=?",
                    (str(e)[:2000], time.time(), run["id"], owner),
                )
                db.event(run["id"], "failed", str(e)[:1000], "error")
            finally:
                active[0] = None
    finally:
        stop.set()
        hb.join(timeout=2)
        scan.join(timeout=3)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()

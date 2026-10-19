from __future__ import annotations

import json
import os
import plistlib
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import httpx
import typer

from . import db
from .config import ROOT, data_dir, load_settings, local_token, save_settings
from .media import binary

app = typer.Typer(help="映知 · 本地录屏学习助手", no_args_is_help=True)
models = typer.Typer()
service = typer.Typer()
app.add_typer(models, name="models")
app.add_typer(service, name="service")


@app.command()
def init():
    db.init_db()
    save_settings(load_settings())
    local_token()
    typer.echo("数据目录：" + str(data_dir()))


@app.command()
def doctor():
    db.init_db()
    from .api import doctor as diagnose

    typer.echo(json.dumps(diagnose(), ensure_ascii=False, indent=2))


@app.command("workflow-worker")
def workflow_worker():
    from .workflows import run_workflow_worker
    run_workflow_worker()


@app.command()
def worker():
    from .worker import run_worker

    run_worker()


@app.command()
def serve(port: int | None = None):
    import uvicorn

    uvicorn.run("video_tutor.api:app", host="127.0.0.1", port=port or load_settings().port)


@app.command()
def start(open_browser: bool = True):
    init()
    settings = load_settings()
    url = f"http://127.0.0.1:{settings.port}"
    # Reuse only a confirmed existing Video Tutor server, never an unrelated process on the same port.
    try:
        with httpx.Client(timeout=2) as c:
            r = c.get(url + "/api/v1/health", cookies={"vt_session": local_token()})
        if r.status_code == 200 and r.json().get("ok"):
            typer.echo("工作台已在运行：" + url)
            if open_browser:
                webbrowser.open(url + "/#token=" + local_token())
            return
    except (httpx.HTTPError, ValueError):
        pass
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "backend")
    children = []

    def stop_signal(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_signal)
    try:
        children = [
            subprocess.Popen([sys.executable, "-m", "video_tutor", "worker"], env=env),
            subprocess.Popen([sys.executable, "-m", "video_tutor", "serve"], env=env),
        ]
        for _ in range(60):
            try:
                if httpx.get(url, timeout=1).status_code < 500:
                    break
            except httpx.HTTPError:
                pass
            if any(p.poll() is not None for p in children):
                raise RuntimeError("服务启动失败，请查看上方错误")
            time.sleep(0.5)
        typer.echo("工作台：" + url + "/#token=" + local_token())
        if open_browser:
            webbrowser.open(url + "/#token=" + local_token())
        while all(p.poll() is None for p in children):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in children:
            if p.poll() is None:
                p.terminate()
        for p in children:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


@app.command("import")
def import_(path: str):
    db.init_db()
    s = load_settings()
    p = Path(path).expanduser().resolve()
    root = p if p.is_dir() else p.parent
    if str(root) not in s.directories:
        s.directories.append(str(root))
        save_settings(s)
    i = db.uid("i_")
    db.execute("INSERT INTO imports VALUES (?,?,?,?,?,?)", (i, str(p), "queued", None, None, time.time()))
    typer.echo(i)


@app.command()
def status():
    db.init_db()
    typer.echo(json.dumps(db.asset_list(), ensure_ascii=False, indent=2))


@app.command()
def pause(run: str):
    typer.echo(db.control(run, "pause"))


@app.command()
def resume(run: str):
    typer.echo(db.control(run, "resume"))


@app.command()
def run(asset: str):
    db.init_db()
    typer.echo(db.create_run(asset, load_settings()))


@app.command()
def export(run: str, format: str = "zip"):
    from .exporting import export_document

    d = db.latest_document(run)
    if not d:
        raise typer.BadParameter("尚未生成笔记")
    folder = data_dir() / "assets" / d["body"]["asset_id"] / "runs" / run / "exports" / f"v{d['version']}"
    export_document(run, d["body"], folder, d["version"])
    typer.echo(str(folder))


@models.command()
def prepare():
    s = load_settings()
    for name in (s.vision_model, s.text_model):
        subprocess.run([binary("ollama"), "pull", name], check=True)
    target = Path(s.whisper_model_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        part = target.with_suffix(target.suffix + ".part")
        subprocess.run(
            [
                "/usr/bin/curl",
                "-L",
                "--fail",
                "--retry",
                "3",
                "-C",
                "-",
                "-o",
                str(part),
                "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin",
            ],
            check=True,
        )
        part.replace(target)
    typer.echo("本地模型已准备完成")


@service.command()
def install():
    label = "local.video-tutor"
    p = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
    p.parent.mkdir(parents=True, exist_ok=True)
    logs = data_dir() / "logs"
    logs.mkdir(exist_ok=True)
    config = {
        "Label": label,
        "ProgramArguments": [sys.executable, "-m", "video_tutor", "start", "--no-open-browser"],
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 15,
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin") + ":/opt/homebrew/bin:/usr/local/bin",
            "PYTHONPATH": str(ROOT / "backend"),
            "VIDEO_TUTOR_DATA": str(data_dir()),
        },
        "StandardOutPath": str(logs / "service.log"),
        "StandardErrorPath": str(logs / "service-error.log"),
    }
    p.write_bytes(plistlib.dumps(config))
    subprocess.run(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(p)], check=True)
    typer.echo("已安装用户级后台服务")


@service.command()
def uninstall():
    p = Path.home() / "Library/LaunchAgents/local.video-tutor.plist"
    if p.exists():
        subprocess.run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}", str(p)], check=False)
        p.unlink()
    typer.echo("后台服务已卸载，资料已保留")


@app.command("study-clip")
def study_clip(source: str, output: str, start_seconds: float = 0, end_seconds: float = 1200):
    """用完整讲解和按主题取图学习片段；生成后仍需证据审阅。"""
    from pathlib import Path

    from .study import StudyRunner, write_json
    from .thematic_study import optimized_study

    source_path, output_path = Path(source).resolve(), Path(output).resolve()
    if source_path == output_path:
        raise typer.BadParameter("输出目录必须独立，不能覆盖原学习资料")
    manifest = json.loads((source_path / "input-manifest.json").read_text())
    start, end = round(start_seconds * 1000), round(end_seconds * 1000)
    if not 0 <= start < end <= manifest["duration_ms"]:
        raise typer.BadParameter("片段时间必须在视频范围内")
    if end - start > 1200000:
        raise typer.BadParameter("当前已验证每次最多20分钟；长课程分片处理后再按主题综合")
    transcript = json.loads((source_path / "source-transcript.json").read_text())
    runner = StudyRunner(output_path)
    started = time.monotonic()
    cached = len(list((output_path / "calls").glob("*/result.json")))
    write_json(output_path / "status.json", {"status": "running", "source": str(source_path),
                                           "start_ms": start, "end_ms": end})
    try:
        optimized_study(runner, transcript, source_path, start, end)
        write_json(output_path / "metrics.json", {**runner.metrics(), "wall_seconds_this_invocation": time.monotonic() - started,
                                                 "cached_calls_before_invocation": cached})
        write_json(output_path / "status.json", {"status": "generated_awaiting_source_review"})
    except Exception as exc:
        write_json(output_path / "status.json", {"status": "failed", "error": str(exc)})
        raise
    typer.echo(f"已生成主题笔记、完整候选画面索引与待查清单：{output_path}；请完成证据审阅后再标记通过。")


if __name__ == "__main__":
    app()

"""Reusable, auditable Codex study calls; never modifies source evidence.

The CLI performs fixed inference with tools disabled by instruction. This is not
an autonomous delegation workflow. Every attempt and reported token count is kept.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)


def timestamp(ms):
    return f"{ms // 60000:02}:{ms // 1000 % 60:02}.{ms % 1000:03}"


def source_text(transcript, start, end):
    return "\n".join(
        f"[{timestamp(t['start_ms'])}–{timestamp(t['end_ms'])}] {t['raw_text']}"
        for t in transcript
        if t["start_ms"] < end and t["end_ms"] > start
    )


def valid_response(label, text):
    """An empty structured gap audit is valid even below the prose length floor."""
    if label.startswith(("rules-", "capability-map-", "knowledge-destinations-")) or "-coverage" in label or label.endswith("-inventory"):
        try:
            return isinstance(json.loads(text.strip().removeprefix("```json\n").removesuffix("```")), dict)
        except (ValueError, TypeError):
            return False
    if label == "visual-gap-audit":
        value = text.strip()
        if value.startswith("```json\n") and value.endswith("```"):
            value = value[8:-3].strip()
        try:
            audit = json.loads(value)
        except (ValueError, TypeError):
            return False
        return isinstance(audit, dict) and all(
            isinstance(audit.get(key), list) for key in ("visual_gaps", "content_gaps", "noncritical_unknowns")
        )
    return len(text.strip()) >= 100


class StudyRunner:
    def __init__(self, output, cli=None, model="gpt-6.1-sol", reasoning="medium", service_tier="default", check=None):
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        from .codex_client import codex_binary

        self.cli = cli or codex_binary()
        self.model, self.reasoning, self.service_tier = model, reasoning, service_tier
        self.check = check or (lambda: None)

    def call(self, label, prompt, frames=(), attempts=2):
        self.check()
        folder = self.output / "calls" / label
        folder.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(prompt.encode())
        digest.update(f"{self.model}|{self.reasoning}|{self.service_tier}|study-v1".encode())
        for frame in frames:
            digest.update(str(frame["id"]).encode())
            digest.update(Path(frame["path"]).read_bytes())
        fingerprint = digest.hexdigest()
        result_path = folder / "result.json"
        if result_path.exists():
            saved = json.loads(result_path.read_text())
            if saved["fingerprint"] != fingerprint:
                raise ValueError(f"Inputs changed for {label}; use a new output directory")
            return (folder / "result.md").read_text()
        (folder / "prompt.txt").write_text(prompt)
        # Empty cwd keeps project instructions/materials out of fixed inference.
        workdir = self.output / "inference-workdir"
        workdir.mkdir(exist_ok=True)
        env = os.environ.copy()
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
            env.pop(key, None)
        previous = len(list(folder.glob("attempt-*.json")))
        for attempt in range(previous + 1, previous + attempts + 1):
            out = folder / f"output-{attempt}.md"
            cmd = [
                self.cli,
                "exec",
                "--ignore-user-config",
                "--ephemeral",
                "--skip-git-repo-check",
                "--json",
                "-s",
                "read-only",
                "-m",
                self.model,
                "-c",
                f"model_reasoning_effort={json.dumps(self.reasoning)}",
                "-c",
                f"service_tier={json.dumps(self.service_tier)}",
                "-c",
                'web_search="disabled"',
                "-c",
                'forced_login_method="chatgpt"',
                "-C",
                str(workdir),
                "-o",
                str(out),
            ]
            for frame in frames:
                cmd.extend(["-i", str(frame["path"])])
            cmd.append("-")
            started = time.monotonic()
            print(f"START {label}: {len(frames)} images", flush=True)
            self.check()
            error, code, interrupted = None, None, None
            with (
                (folder / f"events-{attempt}.jsonl").open("w") as log,
                (folder / f"stderr-{attempt}.log").open("w") as err,
            ):
                try:
                    with (folder / "prompt.txt").open() as input_file:
                        process = subprocess.Popen(cmd, stdin=input_file, stdout=log, stderr=err, env=env)
                        try:
                            while process.poll() is None:
                                self.check()
                                if time.monotonic() - started > 900:
                                    raise subprocess.TimeoutExpired(cmd, 900)
                                time.sleep(0.3)
                            code = process.returncode
                        finally:
                            if process.poll() is None:
                                process.terminate()
                                try:
                                    process.wait(timeout=3)
                                except subprocess.TimeoutExpired:
                                    process.kill()
                                    process.wait()
                except subprocess.TimeoutExpired:
                    error = "timeout after 900s"
                except Exception as exc:
                    interrupted = exc
                    error = str(exc)
            events = []
            for line in (folder / f"events-{attempt}.jsonl").read_text().splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            completed = [e for e in events if e.get("type") == "turn.completed"]
            text = out.read_text() if out.exists() else ""
            record = {
                "label": label,
                "attempt": attempt,
                "seconds": time.monotonic() - started,
                "image_count": len(frames),
                "exit_code": code,
                "fingerprint": fingerprint,
                "usage": completed[-1].get("usage", {}) if completed else {},
                "errors": [e for e in events if e.get("type") in ("error", "turn.failed")],
                "tool_calls": [
                    e
                    for e in events
                    if e.get("item", {}).get("type")
                    in (
                        "command_execution",
                        "mcp_tool_call",
                        "web_search",
                        "file_change",
                        "collab_tool_call",
                        "dynamic_tool_call",
                    )
                ],
            }
            ok = code == 0 and bool(completed) and valid_response(label, text) and not record["tool_calls"]
            record.update(status="completed" if ok else "failed", error=error)
            write_json(folder / f"attempt-{attempt}.json", record)
            print(f"END {label}: {record['status']}, {record['seconds']:.1f}s", flush=True)
            if interrupted:
                raise interrupted
            if ok:
                (folder / "result.md").write_text(text)
                write_json(result_path, record)
                return text
            if attempt < previous + attempts:
                for _ in range(10):
                    self.check()
                    time.sleep(0.3)
        raise RuntimeError(f"{label} failed; all attempts retained in {folder}")

    def metrics(self):
        calls = [json.loads(p.read_text()) for p in self.output.glob("calls/*/attempt-*.json")]
        usage = {}
        for call in calls:
            for key, value in call["usage"].items():
                if isinstance(value, (int, float)):
                    usage[key] = usage.get(key, 0) + value
        return {
            "attempts": len(calls),
            "successful_calls": sum(c["status"] == "completed" for c in calls),
            "summed_call_seconds": sum(c["seconds"] for c in calls),
            "image_submissions": sum(c["image_count"] for c in calls),
            "usage": usage,
            "unmetered_failed_attempts": sum(c["status"] == "failed" and not c["usage"] for c in calls),
        }

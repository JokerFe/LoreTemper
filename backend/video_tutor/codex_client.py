"""Read the installed Codex catalog without extracting or exposing credentials."""

from __future__ import annotations
import json
import os
import select
import shutil
import subprocess
import time
from pathlib import Path


def codex_binary():
    candidates = [
        "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex",
        "/Applications/Codex.app/Contents/Resources/codex",
        shutil.which("codex"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("未找到 Codex 客户端，请先安装并在客户端登录 ChatGPT")


def subscription_env():
    env = os.environ.copy()
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
        env.pop(key, None)
    return env


class CatalogConnection:
    def __enter__(self):
        self.process = subprocess.Popen(
            [codex_binary(), "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=subscription_env(),
        )
        self.buffer = b""
        self.sequence = 0
        self.deadline = time.monotonic() + 25
        self.request("initialize", {"clientInfo": {"name": "video_tutor", "version": "0.2.0"}})
        self.send({"method": "initialized", "params": {}})
        return self

    def send(self, value):
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        self.process.stdin.flush()

    def request(self, method, params):
        self.sequence += 1
        self.send({"id": self.sequence, "method": method, "params": params})
        while time.monotonic() < self.deadline:
            while b"\n" in self.buffer:
                raw, self.buffer = self.buffer.split(b"\n", 1)
                try:
                    response = json.loads(raw)
                except ValueError:
                    continue
                if response.get("id") == self.sequence:
                    if "error" in response:
                        raise RuntimeError(
                            "Codex 无法读取模型或登录状态：" + str(response["error"].get("message", "未知错误"))[:300]
                        )
                    return response.get("result", {})
            if not select.select([self.process.stdout], [], [], min(1, max(0, self.deadline - time.monotonic())))[0]:
                continue
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("Codex 模型服务提前退出，请检查客户端登录状态")
            self.buffer += chunk
        raise RuntimeError("读取 Codex 模型超时，请刷新重试")

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()


def codex_inventory():
    connection = CatalogConnection()
    try:
        with connection as client:
            account = client.request("account/read", {"refreshToken": False}).get("account")
            models, cursor = [], None
            while True:
                result = client.request("model/list", {"limit": 100, "includeHidden": False, "cursor": cursor})
                models.extend(m for m in result.get("data", []) if not m.get("hidden"))
                cursor = result.get("nextCursor")
                if not cursor:
                    break
            return {
                "models": models,
                "logged_in": bool(account and account.get("type") == "chatgpt"),
                "account_type": account.get("type") if account else None,
                "source": "本机 Codex · model/list",
                "fetched_at": time.time(),
            }
    except Exception:
        if hasattr(connection, "process") and connection.process.poll() is None:
            connection.close()
        raise

from __future__ import annotations
import os
import secrets
from pathlib import Path
from typing import Literal
import yaml
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseModel):
    schema_version: int = 1
    directories: list[str] = []
    selected_files: list[str] = []
    analysis_provider: Literal["ollama", "codex"] = "ollama"
    codex_model: str = "gpt-6.1-sol"
    codex_reasoning: str = "medium"
    codex_service_tier: Literal["default", "priority"] = "default"
    output_directory: str = ""
    language: str = "auto"
    glossary: list[str] = []
    asr_backend: Literal["whisper_cpp", "faster_whisper"] = "whisper_cpp"
    asr_model: str = "large-v3-turbo"
    whisper_model_path: str = str(ROOT / "work" / "ggml-large-v3-turbo.bin")
    vision_model: str = "qwen3-vl:8b-instruct"
    text_model: str = "qwen3:14b"
    ollama_url: str = "http://127.0.0.1:11434"
    profile: Literal["balanced", "precise", "fast"] = "balanced"
    auto_import: bool = True
    require_ac_power: bool = True
    min_free_disk_gb: float = Field(10, ge=0, le=1000)
    candidate_fps: float = Field(2, ge=0.2, le=10)
    base_interval_seconds: float = Field(3, ge=0.2, le=60)
    frames_per_minute: int = Field(24, ge=4, le=180)
    image_max_edge: int = Field(1536, ge=512, le=2048)
    frames_per_request: int = Field(4, ge=1, le=4)
    vision_num_ctx: int = Field(8192, ge=4096, le=32768)
    text_num_ctx: int = Field(16384, ge=4096, le=32768)
    asr_chunk_seconds: int = Field(600, ge=10, le=1800)
    request_timeout_seconds: int = Field(600, ge=10, le=3600)
    cache_max_gb: float = Field(15, ge=1, le=1000)
    stable_seconds: int = Field(30, ge=1, le=600)
    scan_seconds: int = Field(30, ge=5, le=600)
    port: int = Field(8765, ge=1024, le=65535)

    @field_validator("ollama_url")
    @classmethod
    def local_only(cls, value: str):
        from urllib.parse import urlparse

        url = urlparse(value)
        if url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1") or url.username:
            raise ValueError("只允许本机 Ollama 服务")
        return value.rstrip("/")

    @field_validator("vision_model", "text_model")
    @classmethod
    def no_cloud(cls, value: str):
        if "cloud" in value.lower() or "/" in value or not value.strip():
            raise ValueError("仅允许本地模型名称，禁止云模型")
        return value

    @field_validator("directories")
    @classmethod
    def normalize_dirs(cls, values):
        return list(dict.fromkeys(str(Path(p).expanduser().resolve()) for p in values))


def data_dir() -> Path:
    p = Path(os.environ.get("VIDEO_TUTOR_DATA", str(ROOT / "data"))).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def load_settings() -> Settings:
    p = data_dir() / "config.yaml"
    return Settings.model_validate(yaml.safe_load(p.read_text()) or {}) if p.exists() else Settings()


def atomic_write(path: Path, content: str | bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + "." + secrets.token_hex(4) + ".tmp")
    temp.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    temp.chmod(0o600)
    temp.replace(path)


def save_settings(s: Settings):
    atomic_write(data_dir() / "config.yaml", yaml.safe_dump(s.model_dump(), allow_unicode=True, sort_keys=False))


def local_token() -> str:
    p = data_dir() / ".session-token"
    if not p.exists():
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_urlsafe(32))
    return p.read_text().strip()

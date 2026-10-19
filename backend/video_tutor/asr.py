from pathlib import Path
import json
import re
from .media import command, binary


def merge_segments(existing, incoming):
    result = list(existing)
    for seg in incoming:
        norm = lambda s: re.sub(r"\W", "", s).lower()
        duplicate = any(
            abs(x["start_ms"] - seg["start_ms"]) < 5000
            and x["end_ms"] >= seg["start_ms"]
            and norm(x["raw_text"]) == norm(seg["raw_text"])
            for x in result[-10:]
        )
        if not duplicate and seg["raw_text"].strip():
            result.append(seg)
    return sorted(result, key=lambda x: x["start_ms"])


def transcribe_window(wav: Path, prefix: Path, settings, offset_ms, duration_ms, check):
    if settings.asr_backend == "faster_whisper":
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError("运行 uv sync --extra faster 安装兼容转写引擎") from e
        # local_files_only avoids implicit network access during processing.
        model = WhisperModel(settings.asr_model, device="cpu", compute_type="int8", local_files_only=True)
        segments, _ = model.transcribe(
            str(wav),
            language=None if settings.language == "auto" else settings.language,
            initial_prompt="、".join(settings.glossary) or None,
            vad_filter=True,
        )
        raw = []
        for seg in segments:
            check()
            raw.append({"offsets": {"from": round(seg.start * 1000), "to": round(seg.end * 1000)}, "text": seg.text})
    else:
        if not Path(settings.whisper_model_path).is_file():
            raise RuntimeError("Whisper 模型未下载，请运行 video-tutor models prepare")
        args = [
            binary("whisper-cli"),
            "-m",
            settings.whisper_model_path,
            "-f",
            str(wav),
            "-l",
            settings.language,
            "-oj",
            "-of",
            str(prefix),
            "-t",
            "4",
            "-np",
        ]
        if settings.glossary:
            args += ["--prompt", "、".join(settings.glossary)]
        command(args, check, 7200)
        raw = json.loads(prefix.with_suffix(".json").read_text())["transcription"]
    result = []
    for i, seg in enumerate(raw):
        a = max(0, offset_ms + int(seg["offsets"]["from"]))
        b = min(duration_ms, offset_ms + int(seg["offsets"]["to"]))
        if b <= a:
            continue
        text = seg["text"].strip()
        result.append(
            {"id": f"t_{a:010d}_{i}", "start_ms": a, "end_ms": b, "raw_text": text, "source": settings.asr_backend}
        )
    return result

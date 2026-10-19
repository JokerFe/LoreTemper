from __future__ import annotations
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
import av
import numpy as np
from PIL import Image

EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".ts"}


def binary(name):
    found = shutil.which(name)
    for root in ("/opt/homebrew/bin", "/usr/local/bin"):
        if found:
            break
        if (Path(root) / name).exists():
            found = str(Path(root) / name)
    if not found:
        raise RuntimeError(f"未找到 {name}，请先安装")
    return found


def command(args, check=lambda: None, timeout=3600):
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        p = subprocess.Popen(args, stdout=out, stderr=err, start_new_session=True)
        started = time.monotonic()
        try:
            while p.poll() is None:
                check()
                if time.monotonic() - started > timeout:
                    raise TimeoutError(f"{Path(args[0]).name} 处理超时")
                time.sleep(0.15)
            if p.returncode:
                err.seek(0)
                raise RuntimeError(f"{Path(args[0]).name} 失败：" + err.read().decode(errors="replace")[-1500:])
            out.seek(0)
            return out.read()
        except BaseException:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()
            raise


def probe(path: Path, check=lambda: None):
    raw = json.loads(
        command(
            [binary("ffprobe"), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], check, 60
        )
    )
    video = next(
        (s for s in raw["streams"] if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")),
        None,
    )
    if not video:
        raise ValueError("文件中没有视频流")
    audio = next((s for s in raw["streams"] if s["codec_type"] == "audio"), None)
    origin = float(raw["format"].get("start_time", video.get("start_time", 0)))
    duration = float(raw["format"].get("duration", video.get("duration", 0)))
    if duration <= 0:
        raise ValueError("无法获取视频时长")
    rotation = next((x.get("rotation", 0) for x in video.get("side_data_list", []) if "rotation" in x), 0)
    return {
        "duration_ms": round(duration * 1000),
        "origin_seconds": origin,
        "video": video,
        "audio": audio,
        "audio_offset_ms": round((float(audio.get("start_time", origin)) - origin) * 1000) if audio else 0,
        "rotation": rotation,
        "format": raw["format"],
        "streams": raw["streams"],
    }


def extract_audio(path, output, meta, check):
    if not meta["audio"]:
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(".tmp.wav")
    command(
        [
            binary("ffmpeg"),
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(path),
            "-map",
            f"0:{meta['audio']['index']}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(tmp),
        ],
        check,
    )
    tmp.replace(output)
    return True


def rotate(image, rotation):
    # ffprobe display matrix rotation is counter-clockwise.
    return image.rotate(float(rotation), expand=True) if rotation else image


def select_frames(path, folder, meta, settings, check, progress, interval=None):
    folder.mkdir(parents=True, exist_ok=True)
    fps = settings.candidate_fps * (2 if settings.profile == "precise" else 0.5 if settings.profile == "fast" else 1)
    base = settings.base_interval_seconds * (
        2 if settings.profile == "fast" else 0.5 if settings.profile == "precise" else 1
    )
    cap = settings.frames_per_minute * (2 if settings.profile == "precise" else 1)
    start, end = interval or (0, meta["duration_ms"])
    next_sample = start / 1000
    previous = None
    last_keep = -1e9
    bucket, bucket_minute = [], -1
    selected, gaps = [], []
    skipped = 0

    def flush():
        nonlocal bucket, skipped
        if not bucket:
            return
        chosen = sorted(sorted(bucket, key=lambda x: x[0], reverse=True)[:cap], key=lambda x: x[1])
        if len(bucket) > cap:
            gaps.append(
                {
                    "start_ms": bucket[0][1],
                    "end_ms": bucket[-1][1],
                    "reason": "画面密集，超出每分钟采样预算",
                    "omitted": len(bucket) - cap,
                }
            )
        for _, ms, img in chosen:
            fid = f"f_{ms:010d}"
            file = folder / (fid + ".jpg")
            img.save(file, quality=92)
            selected.append(
                {
                    "id": fid,
                    "time_ms": ms,
                    "start_ms": ms,
                    "end_ms": ms,
                    "path": str(file),
                    "width": img.width,
                    "height": img.height,
                }
            )
        bucket = []

    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        for frame in container.decode(stream):
            check()
            if frame.pts is None:
                continue
            sec = float(frame.pts * frame.time_base) - meta["origin_seconds"]
            ms = round(sec * 1000)
            if ms < start or sec + 1e-6 < next_sample:
                continue
            if ms > end:
                break
            next_sample = sec + 1 / fps
            progress(max(0, ms - start), end - start)
            minute = ms // 60000
            if minute != bucket_minute:
                flush()
                bucket_minute = minute
            arr = frame.to_ndarray(format="rgb24")
            gray = np.asarray(Image.fromarray(arr).resize((320, 180), Image.Resampling.BILINEAR).convert("L"))
            if previous is None:
                score = 10.0
                changed = True
            else:
                delta = np.abs(gray.astype(np.float32) - previous.astype(np.float32)) / 255
                global_change = float(delta.mean())
                local = max(
                    float(x.mean()) for row in np.array_split(delta, 8, axis=0) for x in np.array_split(row, 8, axis=1)
                )
                score = global_change + local * 2
                changed = global_change > 0.035 or local > 0.015 or int((delta > 0.16).sum()) >= 3
            baseline = sec - last_keep >= base
            # Keep periodic coverage even if near-identical (at most one static frame / 15 s).
            static_cover = sec - last_keep >= 15
            if previous is None or changed or (baseline and score > 0.012) or static_cover:
                img = rotate(Image.fromarray(arr), meta["rotation"])
                bucket.append((score + (1 if static_cover else 0), ms, img))
                # Bound full-resolution memory while preserving strongest candidates.
                if len(bucket) > cap * 2:
                    bucket = sorted(bucket, key=lambda x: x[0], reverse=True)[:cap]
                    skipped += cap + 1
                last_keep = sec
            previous = gray
    flush()
    if not selected:
        raise ValueError("该时间范围没有可解码画面")
    for i, item in enumerate(selected):
        item["end_ms"] = selected[i + 1]["time_ms"] if i + 1 < len(selected) else end
    if skipped:
        gaps.append({"start_ms": start, "end_ms": end, "reason": "候选帧内存预算筛选", "omitted": skipped})
    return {"frames": selected, "coverage_gaps": gaps, "start_ms": start, "end_ms": end, "candidate_fps": fps}


def make_proxy(source, output, check):
    tmp = output.with_suffix(".tmp.mp4")
    command(
        [
            binary("ffmpeg"),
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-vf",
            r"scale=trunc(min(1280\,iw)/2)*2:-2",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(tmp),
        ],
        check,
    )
    tmp.replace(output)

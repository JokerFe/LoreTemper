from __future__ import annotations
import json
import shutil
import time
from pathlib import Path
from PIL import Image
from . import db
from .asr import merge_segments, transcribe_window
from .config import Settings, atomic_write, data_dir
from .exporting import export_document
from .llm import GENERATION_POLICY_VERSION, LocalLLM, model_inventory, prompt
from .keyframes import keyframe_plan
from .media import binary, command, extract_audio, make_proxy, probe, select_frames
from .schemas import Chapter, Observation, Overview


class StopWork(Exception):
    pass


def validate_refs(value, allowed):
    if isinstance(value, dict):
        for k, v in value.items():
            if k in ("frame_ids", "evidence_ids", "summary_evidence_ids"):
                bad = set(v) - set(allowed)
                if bad:
                    raise ValueError("模型引用了不存在的证据：" + ", ".join(sorted(bad)))
            elif k == "frame_id" and v not in allowed:
                raise ValueError("模型文字引用了不存在的画面")
            else:
                validate_refs(v, allowed)
    elif isinstance(value, list):
        for v in value:
            validate_refs(v, allowed)


def anchor_step_times(steps, timeline, start, end):
    """Playback anchors come from cited media, never model-invented timestamps."""
    for step in steps:
        spans = [timeline[eid] for eid in step["evidence_ids"] if eid in timeline]
        spans = [(max(start, a), min(end, b)) for a, b in spans if b >= start and a <= end]
        if not spans:
            raise ValueError("步骤没有所在时间范围内的证据")
        step["start_ms"] = min(a for a, _ in spans)
        step["end_ms"] = max(b for _, b in spans)
    steps.sort(key=lambda step: step["start_ms"])


def frame_groups(frames, size):
    groups = []
    group = []
    for f in frames:
        if group and (len(group) >= size or f["time_ms"] - group[0]["time_ms"] > 15000):
            groups.append(group)
            group = []
        group.append(f)
    if group:
        groups.append(group)
    return groups


def compact_observations(observations):
    """Remove repeated wording in fusion inputs while retaining every citation.

    Original observations/screenshots remain unchanged. Match exact text and
    uncertainty only; different values or uncertainty must never be merged.
    """
    result = []
    for observation in observations:
        value = {k: v for k, v in observation.items() if k != "id"}
        groups = {}
        for item in observation.get("visible_texts", []):
            groups.setdefault((item["text"], item.get("uncertainty", "")), []).append(item)
        texts = []
        for (text, uncertainty), items in groups.items():
            if len(items) == 1:
                texts.append(dict(items[0]))
            else:
                texts.append(
                    {
                        "text": text,
                        "uncertainty": uncertainty,
                        "frame_ids": list(dict.fromkeys(item["frame_id"] for item in items)),
                    }
                )
        value["visible_texts"] = texts
        result.append(value)
    return result


class Pipeline:
    def __init__(self, run, owner):
        self.run = run
        self.id = run["id"]
        self.owner = owner
        self.generation = run["generation"]
        self.settings = Settings.model_validate_json(run["config_json"])
        self.options = json.loads(run["options_json"])
        self.folder = data_dir() / "assets" / run["asset_id"] / "runs" / self.id
        self.folder.mkdir(parents=True, exist_ok=True)
        self.metrics = []
        self.warnings = []
        self.llm = LocalLLM(self.settings, self.check, self.metrics)
        self.last_progress = 0.0
        self.last_disk_check = 0.0

    def check(self):
        if time.monotonic() - self.last_disk_check > 5:
            self.last_disk_check = time.monotonic()
            if shutil.disk_usage(data_dir()).free < self.settings.min_free_disk_gb * 1e9:
                db.execute("UPDATE runs SET status='pause_requested' WHERE id=? AND status='running'", (self.id,))
                db.event(self.id, "disk_low", "磁盘空间不足，任务暂停", "warning")
        r = db.one("SELECT status,generation,lease_owner FROM runs WHERE id=?", (self.id,))
        if not r or r["generation"] != self.generation or r["lease_owner"] != self.owner:
            raise StopWork("任务已失去执行权")
        if r["status"] in ("pause_requested", "cancel_requested"):
            status = "paused" if r["status"] == "pause_requested" else "cancelled"
            db.execute(
                "UPDATE runs SET status=?,lease_owner=NULL,lease_expires=NULL WHERE id=? AND generation=?",
                (status, self.id, self.generation),
            )
            db.event(self.id, status, "任务已暂停" if status == "paused" else "任务已取消")
            raise StopWork(status)

    def stage(self, name, done=0, total=0):
        self.last_progress = 0.0
        self.check()
        db.execute("UPDATE runs SET stage=?,progress_done=?,progress_total=? WHERE id=?", (name, done, total, self.id))
        db.event(self.id, "stage", name)

    def progress(self, done, total):
        if time.monotonic() - self.last_progress > 0.75 or done == total:
            db.execute("UPDATE runs SET progress_done=?,progress_total=? WHERE id=?", (done, total, self.id))
            self.last_progress = time.monotonic()

    def unit(self, stage, key, inputs, fn):
        self.check()
        fingerprint_inputs = {"pipeline": 2, "inputs": inputs}
        if stage in ("analyze_frames", "fuse", "summarize"):
            fingerprint_inputs["generation_policy"] = GENERATION_POLICY_VERSION
        fingerprint = db.digest(fingerprint_inputs)
        cached = db.one(
            "SELECT * FROM units WHERE run_id=? AND stage=? AND unit_key=? AND input_hash=?",
            (self.id, stage, key, fingerprint),
        )
        if not cached:
            cached = db.one(
                """SELECT u.* FROM units u JOIN runs r ON u.run_id=r.id
            WHERE r.asset_id=? AND u.stage=? AND u.unit_key=? AND u.input_hash=? ORDER BY r.created_at DESC LIMIT 1""",
                (self.run["asset_id"], stage, key, fingerprint),
            )
        if cached:
            path = data_dir() / cached["artifact_path"]
            if path.is_file() and db.file_sha(path) == cached["artifact_sha"]:
                value = json.loads(path.read_text())
                # Media-dependent cache entries must still have every referenced file.
                files = [Path(x["path"]) for x in value.get("frames", [])] if isinstance(value, dict) else []
                if all(p.exists() for p in files):
                    if cached["run_id"] != self.id:
                        db.execute(
                            "INSERT OR REPLACE INTO units VALUES (?,?,?,?,?,?,?)",
                            (self.id, stage, key, fingerprint, cached["artifact_path"], cached["artifact_sha"], 0),
                        )
                    return value
        started = time.monotonic()
        value = fn()
        self.check()
        path = self.folder / "checkpoints" / stage / (key + ".json")
        atomic_write(path, db.dumps(value))
        db.execute(
            "INSERT OR REPLACE INTO units VALUES (?,?,?,?,?,?,?)",
            (
                self.id,
                stage,
                key,
                fingerprint,
                str(path.relative_to(data_dir())),
                db.file_sha(path),
                time.monotonic() - started,
            ),
        )
        return value

    def execute(self):
        started = time.time()
        try:
            return self.process(started)
        finally:
            self.llm.unload()
            atomic_write(
                self.folder / "metrics.json",
                db.dumps({"requests": self.metrics, "elapsed_seconds": time.time() - started}),
            )
            if getattr(self, "output_folder", None):
                try:
                    shutil.copy2(self.folder / "metrics.json", self.output_folder / "processing-metrics.json")
                except OSError as exc:
                    db.event(self.id, "output_metrics_error", str(exc)[:400], "warning")

    def process(self, started):
        self.stage("probe")
        asset = db.one("SELECT * FROM assets WHERE id=?", (self.run["asset_id"],))
        sources = db.rows("SELECT path FROM sources WHERE asset_id=?", (asset["id"],))
        source = next((Path(s["path"]) for s in sources if Path(s["path"]).is_file()), None)
        if source is None:
            raise ValueError("原视频已移动或不可用，请重新导入原文件")
        if db.file_sha(source) != asset["sha256"]:
            raise ValueError("原视频内容已改变，请重新导入新版本")
        media = self.unit("probe", "metadata", asset["sha256"], lambda: probe(source, self.check))
        db.execute("UPDATE assets SET media_json=? WHERE id=?", (db.dumps(media), asset["id"]))
        duration = media["duration_ms"]
        start = int(self.options.get("start_ms", 0))
        end = int(self.options.get("end_ms", duration))
        if not 0 <= start < end <= duration:
            raise ValueError("片段时间范围无效")
        partial = start != 0 or end != duration
        if partial:
            self.warnings.append(f"本次仅分析 {start / 1000:.1f}–{end / 1000:.1f} 秒，其余内容未分析")
        digests = {}
        if self.settings.analysis_provider == "ollama":
            inventories = model_inventory(self.settings)
            digests = {x["name"]: x["digest"] for x in inventories}
            for name in (self.settings.vision_model, self.settings.text_model):
                if name not in digests and name + ":latest" not in digests:
                    raise RuntimeError(f"尚未下载本地模型 {name}，请运行 models prepare")
        transcript = []
        if media["audio"]:
            self.stage("extract_audio")
            wav = self.folder / "temp" / "audio.wav"
            if not wav.exists():
                extract_audio(source, wav, media, self.check)
            self.stage("transcribe")
            offset = media["audio_offset_ms"]
            chunk_ms = self.settings.asr_chunk_seconds * 1000
            positions = list(range(max(0, start - offset), max(1, end - offset), chunk_ms))
            # The model file is identical for every audio window in this run.
            model_path = Path(self.settings.whisper_model_path)
            asr_version = {
                "name": self.settings.asr_model,
                "backend": self.settings.asr_backend,
                "model_size": model_path.stat().st_size if model_path.exists() else None,
                "model_sha256": db.file_sha(model_path) if model_path.exists() else None,
                "model_mtime": model_path.stat().st_mtime_ns if model_path.exists() else None,
            }
            for i, position in enumerate(positions):
                self.progress(i, len(positions))
                window_start = max(0, position - (5000 if i else 0))
                length = min(chunk_ms + 5000, end - offset - window_start)

                def transcribe(position=position, window_start=window_start, length=length):
                    part = self.folder / "temp" / f"audio-{position}.wav"
                    prefix = part.with_suffix("")
                    command(
                        [
                            binary("ffmpeg"),
                            "-nostdin",
                            "-v",
                            "error",
                            "-y",
                            "-ss",
                            str(window_start / 1000),
                            "-i",
                            str(wav),
                            "-t",
                            str(length / 1000),
                            str(part),
                        ],
                        self.check,
                    )
                    result = transcribe_window(part, prefix, self.settings, window_start + offset, duration, self.check)
                    part.unlink(missing_ok=True)
                    prefix.with_suffix(".json").unlink(missing_ok=True)
                    return [t for t in result if t["end_ms"] > start and t["start_ms"] < end]

                segment = self.unit(
                    "transcribe",
                    str(position),
                    [
                        asset["sha256"],
                        window_start,
                        length,
                        offset,
                        asr_version,
                        self.settings.language,
                        self.settings.glossary,
                    ],
                    transcribe,
                )
                transcript = merge_segments(transcript, segment)
            self.progress(len(positions), len(positions))
            wav.unlink(missing_ok=True)
            if not transcript:
                self.warnings.append("未识别出可用语音，本次主要依据画面")
        else:
            self.warnings.append("视频无音轨，已使用纯画面分析")
        self.stage("select_frames")
        sampling = self.unit(
            "select_frames",
            "frames",
            [
                asset["sha256"],
                start,
                end,
                self.settings.profile,
                self.settings.candidate_fps,
                self.settings.base_interval_seconds,
                self.settings.frames_per_minute,
            ],
            lambda: select_frames(
                source, self.folder / "frames", media, self.settings, self.check, self.progress, (start, end)
            ),
        )
        frames = sampling["frames"]
        for f in frames:
            f["artifact_id"] = db.add_artifact(self.id, Path(f["path"]), "frame")
        self.warnings += [
            f"{x['start_ms'] / 1000:.0f}–{x['end_ms'] / 1000:.0f} 秒：{x['reason']}" for x in sampling["coverage_gaps"]
        ]
        if self.settings.analysis_provider == "codex":
            from .codex_pipeline import process_codex
            return process_codex(self, asset, media, source, transcript, frames, sampling, start, end, started)
        self.stage("analyze_frames")
        groups = frame_groups(frames, self.settings.frames_per_request)
        plan = [(f"{i:05}", group) for i, group in enumerate(groups)]
        if self.options.get("visual_analysis") == "keyframes":
            plan = keyframe_plan(
                groups,
                transcript,
                self.settings.frames_per_request,
                self.options.get("preserve_visual_groups", 0),
                self.check,
            )
            self.warnings.append(
                "关键画面模式：保留完整语音和代表性画面；短暂点击、细微视觉变化可能未覆盖，可按时间回看原视频。"
            )
        sampling["visual_analysis"] = {
            "mode": self.options.get("visual_analysis", "full"),
            "original_groups": len(groups),
            "planned_groups": len(plan),
            "analyzed_frame_ids": [f["id"] for _, group in plan for f in group],
        }
        atomic_write(self.folder / "visual-plan.json", db.dumps(sampling["visual_analysis"]))
        observations = []
        vision_prompt = prompt("vision")
        for i, (group_key, group) in enumerate(plan):
            self.progress(i, len(plan))
            a = group[0]["time_ms"]
            b = max(group[-1]["time_ms"] + 1000, a + 1000)
            speech = [t for t in transcript if t["end_ms"] >= a - 3000 and t["start_ms"] <= b + 3000]

            def analyze(group=group, speech=speech):
                result = self.llm.chat(
                    self.settings.vision_model, Observation, vision_prompt, {"transcript": speech}, group
                )
                validate_refs(result, {f["id"] for f in group})
                return result

            obs = self.unit(
                "analyze_frames",
                group_key,
                [
                    [(f["id"], db.file_sha(Path(f["path"]))) for f in group],
                    speech,
                    digests.get(self.settings.vision_model),
                    vision_prompt,
                    self.settings.image_max_edge,
                    self.settings.vision_num_ctx,
                ],
                analyze,
            )
            # One automatic detailed pass: four native-resolution quadrants of a representative frame.
            if obs.get("needs_crop"):
                parent = group[-1]
                crop_frames = []
                with Image.open(parent["path"]) as im:
                    w, h = im.size
                    for n, box in enumerate(
                        [(0, 0, w // 2, h // 2), (w // 2, 0, w, h // 2), (0, h // 2, w // 2, h), (w // 2, h // 2, w, h)]
                    ):
                        fid = parent["id"] + f"_crop{n}"
                        p = self.folder / "frames" / (fid + ".jpg")
                        p.parent.mkdir(exist_ok=True)
                        im.crop(box).save(p, quality=95)
                        crop_frames.append(
                            {
                                "id": fid,
                                "time_ms": parent["time_ms"],
                                "start_ms": parent["start_ms"],
                                "end_ms": parent["end_ms"],
                                "path": str(p),
                                "parent_id": parent["id"],
                                "bbox": [box[0] / w, box[1] / h, box[2] / w, box[3] / h],
                                "artifact_id": db.add_artifact(self.id, p, "frame"),
                            }
                        )

                def crop_analyze():
                    result = self.llm.chat(
                        self.settings.vision_model,
                        Observation,
                        vision_prompt,
                        {"instruction": "这是同一画面的四个局部裁切，只补充能确认的文字。", "transcript": speech},
                        crop_frames,
                    )
                    validate_refs(result, {f["id"] for f in crop_frames})
                    return result

                extra = self.unit(
                    "analyze_frames",
                    f"{group_key}-crop",
                    [obs, digests.get(self.settings.vision_model), vision_prompt],
                    crop_analyze,
                )
                frames.extend(crop_frames)
                observations.append({"id": f"o_{i}_crop", "start_ms": a, "end_ms": b, **extra})
            observations.append({"id": f"o_{i}", "start_ms": a, "end_ms": b, **obs})
        self.progress(len(plan), len(plan))
        self.llm.unload(self.settings.vision_model)
        self.stage("fuse")
        chapters = []
        timeline = {f["id"]: (f["time_ms"], f["time_ms"]) for f in frames}
        timeline.update({t["id"]: (t["start_ms"], t["end_ms"]) for t in transcript})
        # Bounded 90-second chunks; split further if a high-density chunk exceeds a conservative character budget.
        spans = [(p, min(p + 90000, end)) for p in range(start, end, 90000)]
        i = 0
        while i < len(spans):
            a, b = spans[i]
            local_obs = [o for o in observations if o["start_ms"] < b and o["end_ms"] > a]
            local_t = [t for t in transcript if t["start_ms"] < b and t["end_ms"] > a]
            payload = {
                "start_ms": a,
                "end_ms": b,
                "observations": compact_observations(local_obs),
                "transcript": local_t,
                "evidence_timeline": {
                    eid: {"start_ms": timeline[eid][0], "end_ms": timeline[eid][1]}
                    for eid in ({fid for o in local_obs for fid in o["frame_ids"]} | {t["id"] for t in local_t})
                    if eid in timeline
                },
            }
            if len(db.dumps(payload)) > 18000 and b - a > 15000:
                mid = (a + b) // 2
                spans[i : i + 1] = [(a, mid), (mid, b)]
                continue
            allowed = {fid for o in local_obs for fid in o["frame_ids"]} | {t["id"] for t in local_t}
            self.progress(i, len(spans))
            if not allowed:
                chapters.append(
                    {
                        "title": f"{a // 1000} 秒处证据不足",
                        "summary": "该片段没有足够的语音或画面记录。",
                        "steps": [],
                        "unresolved_items": ["需精细重跑此片段"],
                    }
                )
                i += 1
                continue

            def fuse(payload=payload, allowed=allowed, a=a, b=b):
                value = self.llm.chat(self.settings.text_model, Chapter, prompt("chapter"), payload)
                validate_refs(value, allowed)
                for step in value["steps"]:
                    if not a <= step["start_ms"] <= step["end_ms"] <= b:
                        raise ValueError("模型步骤时间超出所在片段")
                anchor_step_times(value["steps"], timeline, a, b)
                return value

            chapter = self.unit(
                "fuse", f"{a}-{b}", [payload, digests.get(self.settings.text_model), prompt("chapter")], fuse
            )
            chapter = {**chapter, "evidence_ids": sorted(allowed)}
            chapters.append(chapter)
            i += 1
        self.stage("summarize")
        all_ids = {f["id"] for f in frames} | {t["id"] for t in transcript}
        # Reduce long recordings recursively, preserving evidence IDs at every level.
        outline = [
            {
                "title": c["title"],
                "summary": c["summary"],
                "evidence_ids": c.get("evidence_ids", []),
                "steps": [{"title": s["title"], "evidence_ids": s["evidence_ids"]} for s in c["steps"]],
                "unresolved_items": c["unresolved_items"],
            }
            for c in chapters
        ]
        level = 0
        while len(db.dumps(outline)) > 16000:
            batches = []
            current = []
            chars = 0
            for item in outline:
                text = db.dumps(item)
                if current and chars + len(text) > 10000:
                    batches.append(current)
                    current = []
                    chars = 0
                current.append(item)
                chars += len(text)
            if current:
                batches.append(current)
            reduced = []
            for n, batch in enumerate(batches):

                def reduce_batch(batch=batch):
                    v = self.llm.chat(self.settings.text_model, Overview, prompt("overview"), {"chapters": batch})
                    validate_refs(v, all_ids)
                    return v

                v = self.unit(
                    "summarize",
                    f"level{level}-{n}",
                    [batch, digests.get(self.settings.text_model), prompt("overview")],
                    reduce_batch,
                )
                reduced.append(
                    {"title": v["title"], "summary": v["summary"], "evidence_ids": v["summary_evidence_ids"]}
                )
            if len(db.dumps(reduced)) >= len(db.dumps(outline)):
                raise ValueError("长片归纳无法收敛，请拆分视频范围")
            outline = reduced
            level += 1

        def overview():
            v = self.llm.chat(
                self.settings.text_model, Overview, prompt("overview"), {"chapters": outline, "warnings": self.warnings}
            )
            validate_refs(v, all_ids)
            return v

        summary = self.unit(
            "summarize",
            "overview",
            [outline, self.warnings, digests.get(self.settings.text_model), prompt("overview")],
            overview,
        )
        thematic_summary = None
        if self.settings.learning_quality == "joker-ui-v2":
            from .note_quality import summarize_local
            thematic_summary = summarize_local(self, chapters, transcript, frames)
        self.llm.unload(self.settings.text_model)
        self.stage("validate")
        doc = {
            "schema_version": 1,
            "asset_id": asset["id"],
            "run_id": self.id,
            "source_title": asset["title"],
            "duration_ms": duration,
            "overview": summary,
            "chapters": chapters,
            "transcript": transcript,
            "frames": {f["id"]: {k: v for k, v in f.items() if k != "path"} for f in frames},
            "observations": observations,
            "warnings": self.warnings,
            "coverage": {k: v for k, v in sampling.items() if k != "frames"},
            "user_notes": "",
            "provenance": {
                "source_sha256": asset["sha256"],
                "models": digests,
                "settings": self.settings.model_dump(),
                "run_options": self.options,
                "pipeline_version": 2,
                "generation_policy_version": GENERATION_POLICY_VERSION,
                "fusion_evidence_encoding": "deduplicated-v1",
                "started_at": started,
                "elapsed_seconds": time.time() - started,
                "prompts": {p: db.digest(prompt(p)) for p in ("vision", "chapter", "overview")},
            },
        }
        if thematic_summary is not None:
            doc['summary_markdown'] = thematic_summary
            doc['provenance']['note_quality_profile'] = self.settings.learning_quality
            doc['provenance']['source_review'] = 'requires_independent_source_review'
        validate_refs(doc, all_ids)
        self.check()
        previous = db.latest_document(self.id)
        if previous and previous["origin"] == "user":
            doc["user_notes"] = previous["body"].get("user_notes", "")
        version = db.save_document(self.id, doc)
        self.stage("export")
        exports = export_document(self.id, doc, self.folder / "exports" / f"v{version}", version)
        atomic_write(
            self.folder / "manifest.json",
            db.dumps(
                {"run_id": self.id, "exports": exports, "warnings": self.warnings, "provenance": doc["provenance"]}
            ),
        )
        if self.settings.output_directory:
            from .codex_pipeline import publish_output
            publish_output(self, self.folder / "exports" / f"v{version}")
        self.stage("index")
        from .retrieval import index_document

        index_document(doc)
        # Generate a browser-compatible proxy only when needed, preserving original timeline.
        codec = media["video"].get("codec_name")
        if source.suffix.lower() not in (".mp4", ".m4v", ".webm") or codec not in ("h264", "vp8", "vp9", "av1"):
            self.stage("prepare_playback")
            proxy = self.folder / "playback.mp4"
            if not proxy.exists():
                make_proxy(source, proxy, self.check)
            db.add_artifact(self.id, proxy, "proxy")
        self.check()
        status = (
            "succeeded_with_warnings"
            if self.warnings
            or summary["unresolved_items"]
            or any(s["uncertainties"] for c in chapters for s in c["steps"])
            else "succeeded"
        )
        db.execute(
            "UPDATE runs SET status=?,stage=?,warnings_json=?,finished_at=?,lease_owner=NULL,lease_expires=NULL WHERE id=? AND generation=?",
            (status, "complete", db.dumps(self.warnings), time.time(), self.id, self.generation),
        )
        db.event(self.id, "complete", "图文笔记已生成")
        return doc

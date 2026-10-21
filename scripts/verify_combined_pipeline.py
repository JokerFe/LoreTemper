"""Real-model, isolated two-batch verification. Requires Codex login and local ASR.

Creates three explicitly synthetic voiced slide videos on macOS. Only process
launching is replaced with synchronous queue driving; media, ASR, model calls,
note export, handoff, Skill generation and retention use production code.
Reusing --workspace resumes frozen jobs and cached inference without deleting files.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
from pathlib import Path


def fixtures(root):
    import textwrap

    from PIL import Image, ImageDraw, ImageFont
    from video_tutor.media import binary

    lessons = [
        (
            "01-hierarchy",
            "Visual hierarchy",
            (
                "For this task card, make the task title larger than the supporting metadata. "
                "Use one prominent primary action so the next step is clear. The example size is not a universal rule."
            ),
        ),
        (
            "02-grouping",
            "Group related content",
            (
                "Place a label close to its related input. Leave more space between different groups than inside a group. "
                "In this form, proximity explains which label belongs to which field. These example gaps depend on content."
            ),
        ),
        (
            "03-conditions",
            "Hierarchy: additional conditions",
            (
                "When a screen has several equally important tasks, avoid making every action visually dominant. "
                "Group related actions and choose emphasis for the current user goal. This adds a condition to the earlier hierarchy lesson."
            ),
        ),
    ]
    folder = root / "synthetic-inputs"
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for stem, title, narration in lessons:
        video = folder / (stem + ".mp4")
        paths.append(video)
        if video.exists():
            continue
        (folder / (stem + ".txt")).write_text(narration)
        image = Image.new("RGB", (1280, 720), "#f5f5ef")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 32)
        small = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 23)
        draw.text((60, 45), "SYNTHETIC TEST LESSON — " + title, font=font, fill="#1c4938")
        draw.multiline_text((60, 155), "\n".join(textwrap.wrap(narration, 65)), font=font, fill="#283c32", spacing=22)
        draw.text((60, 640), "Generated test content. No real course or personal material.", font=small, fill="#56695e")
        png, audio = folder / (stem + ".png"), folder / (stem + ".aiff")
        image.save(png)
        subprocess.run(["say", "-v", "Samantha", "-r", "165", "-o", str(audio), narration], check=True)
        duration = subprocess.check_output(
            [
                binary("ffprobe"),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio),
            ],
            text=True,
        ).strip()
        subprocess.run(
            [
                binary("ffmpeg"),
                "-v",
                "error",
                "-y",
                "-loop",
                "1",
                "-i",
                str(png),
                "-i",
                str(audio),
                "-r",
                "5",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                "-t",
                duration,
                str(video),
            ],
            check=True,
        )
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()
    root = args.workspace.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["VIDEO_TUTOR_DATA"] = str(root / "isolated-data")
    from video_tutor import analysis_control, db, workflows
    from video_tutor.config import Settings, save_settings
    from video_tutor.pipeline import Pipeline
    from video_tutor.study import write_json
    from video_tutor.study_library import library_root, read_catalog, verify_entry
    from video_tutor.worker import claim

    db.init_db()
    workflows.init()
    if not (root / "isolated-data/config.yaml").exists():
        save_settings(Settings(require_ac_power=False, min_free_disk_gb=3, language="en", auto_import=False))
    # This verifier owns its isolated DB and drives the production queues in-process.
    workflows.ensure_workflow_worker = lambda: None
    analysis_control.ensure_worker = lambda: None
    videos = fixtures(root)
    output = root / "output"
    reports, prior_hashes = [], {}
    for index, group in enumerate([videos[:2], videos[2:]], 1):
        ident = f"real-chain-batch-{index}"
        print(f"Starting batch {index}: {len(group)} synthetic videos", flush=True)
        config = workflows.WorkflowInput(
            mode="combined",
            video_paths=[str(p) for p in group],
            auto_input=False,
            output_directory=str(output),
            batch_label=f"真实模型验证·第{index}批",
            request_id=ident,
            include_previous_notes=index > 1,
            skill_name="synthetic-design-guide",
            study=workflows.StageConfig(provider="codex", model="gpt-6.1-sol", reasoning="medium"),
            skill=workflows.StageConfig(provider="codex", model="gpt-6.1-sol", reasoning="medium"),
        )
        workflows.submit(config)

        def job(ident=ident):
            return db.one("SELECT * FROM workflows WHERE id=?", (ident,))

        if job()["status"] == "failed":
            workflows.control(ident, "retry")
        if job()["status"] != "succeeded":
            if not job()["batch_id"]:
                workflows.tick(job())
            analysis_control.process_study_batches(threading.Event())
            while run := claim("isolated-chain-verifier"):
                print("Learning " + run["id"], flush=True)
                try:
                    Pipeline(run, "isolated-chain-verifier").execute()
                except Exception as exc:
                    db.execute(
                        "UPDATE runs SET status='failed',error=?,lease_owner=NULL WHERE id=?", (str(exc), run["id"])
                    )
                    try:
                        workflows.tick(job())
                    except (ValueError, RuntimeError, OSError) as workflow_exc:
                        workflows.update(ident, status="failed", error=f"{exc}; archive: {workflow_exc}")
                    write_json(
                        root / "verification-failure.json",
                        {"status": "failed", "stage": "learning", "error": str(exc), "run_id": run["id"]},
                    )
                    raise
                print("Finished " + run["id"], flush=True)
            print("Freezing handoff and generating Skill", flush=True)
            workflows.tick(job())
        assert job()["status"] == "succeeded", job()
        result = json.loads(job()["result_json"])
        library = library_root(output)
        catalog = read_catalog(library)
        for entry in catalog["entries"]:
            verify_entry(library / entry["directory"])
        assert all(p.is_file() and db.file_sha(p) == sha for p, sha in prior_hashes.items()), "Earlier evidence changed"
        prior_hashes = {p: db.file_sha(p) for p in (library / "entries").rglob("*") if p.is_file()}
        package = Path(result["skill_file"]).parent
        destinations = json.loads((package / "references/coverage/knowledge-destinations.json").read_text())
        handoff = json.loads(Path(result["handoff_manifest"]).read_text())
        expected = sum(len(json.loads(Path(s["knowledge_path"]).read_text())["items"]) for s in handoff["sources"])
        assert len(destinations["items"]) == expected and not destinations["missing_inventories"]
        assert result["source_count"] == (2 if index == 1 else 3)
        assert len({x["knowledge_id"] for x in destinations["items"]}) == expected
        reports.append(
            {
                "batch": index,
                "result": result,
                "knowledge_count": expected,
                "destinations": destinations["counts"],
                "retained_files": len(prior_hashes),
                "earlier_evidence_unchanged": True,
            }
        )
        write_json(
            root / "verification.json",
            {
                "status": "running" if index == 1 else "passed",
                "synthetic_inputs": True,
                "real_asr_and_models": True,
                "installed_skill": False,
                "batches": reports,
            },
        )
        print(json.dumps(reports[-1], ensure_ascii=False), flush=True)
    print("PASS: " + str(root / "verification.json"), flush=True)


if __name__ == "__main__":
    main()

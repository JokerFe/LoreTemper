import json
from pathlib import Path

import pytest
from video_tutor import db
from video_tutor.import_learning import import_catalog
from video_tutor.study import write_json
from video_tutor.study_library import library_root, previous_notes, read_catalog, verify_entry


def source(tmp_path):
    project = tmp_path / "project with spaces"
    folder = project / "learning/batches/batch-001/lesson"
    folder.mkdir(parents=True)
    (folder / "notes.md").write_text("# 原笔记\n\n负形与视觉重量\n![案例](images/截图.jpg)")
    (folder / "notes.html").write_text(
        '<html><head><meta charset="utf-8"></head><body>负形<img src="images/截图.jpg"></body></html>'
    )
    (folder / "images").mkdir()
    (folder / "images/截图.jpg").write_bytes(b"original-image-fixture")
    write_json(folder / "status.json", {"status": "completed"})
    (folder / "review.md").write_text("原关键证据审阅")
    catalog = project / "learning/catalog.json"
    write_json(
        catalog,
        {
            "batches": [
                {
                    "id": "batch-001",
                    "label": "首批课程",
                    "videos": [
                        {
                            "asset_id": "v_1",
                            "title": "1原课程",
                            "reviewed_note": str((folder / "notes.md").relative_to(project)),
                            "codex_study": {"status_file": str((folder / "status.json").relative_to(project))},
                            "review_status": "full_coverage_key_evidence_reviewed",
                            "sources": [str(project / "missing-video.mp4")],
                            "sha256": "original-registered-video-hash",
                        }
                    ],
                }
            ]
        },
    )
    return catalog, folder


def test_originals_idempotency_versions_and_missing_inventory(tmp_path):
    catalog, folder = source(tmp_path)
    original_hashes = {str(p): db.file_sha(p) for p in folder.rglob("*") if p.is_file()}
    output = tmp_path / "output"
    result = import_catalog(catalog, output)
    assert result["batches"] == {"batch-001": 1}
    import_catalog(catalog, output)
    root = library_root(output)
    records = read_catalog(root)
    assert len(records["entries"]) == 1
    assert len(records["batches"]) == 1
    entry = records["entries"][0]
    assert entry["knowledge_count"] is None
    assert entry["review_status"] == "full_coverage_key_evidence_reviewed"
    snapshot = root / entry["directory"]
    assert (snapshot / "notes.md").read_bytes() == (folder / "notes.md").read_bytes()
    assert (snapshot / "original-notes.html").read_bytes() == (folder / "notes.html").read_bytes()
    assert "<base href=" in (snapshot / "document.html").read_text()
    assert not json.loads((snapshot / "provenance.json").read_text())["source_video_availability"][0]["exists"]
    assert previous_notes(output)[0]["knowledge_path"] is None
    assert {str(p): db.file_sha(p) for p in folder.rglob("*") if p.is_file()} == original_hashes
    (folder / "notes.md").write_text("# 更新笔记\n新的条件")
    import_catalog(catalog, output)
    records = read_catalog(root)
    assert len(records["entries"]) == 2
    assert len(records["batches"][0]["entry_ids"]) == 2
    assert "负形" in (snapshot / "notes.md").read_text()
    assert Path(previous_notes(output)[0]["path"]).read_text() == "# 更新笔记\n新的条件"
    for entry in records["entries"]:
        verify_entry(root / entry["directory"])


def test_preflight_preserves_index_when_course_is_incomplete(tmp_path):
    catalog, folder = source(tmp_path)
    output = tmp_path / "output"
    import_catalog(catalog, output)
    root = library_root(output)
    before = (root / "catalog.json").read_bytes()
    write_json(folder / "status.json", {"status": "running"})
    with pytest.raises(ValueError, match="尚未完成"):
        import_catalog(catalog, output)
    assert (root / "catalog.json").read_bytes() == before
    write_json(folder / "status.json", {"status": "completed"})
    (folder / "notes.html").unlink()
    with pytest.raises(FileNotFoundError):
        import_catalog(catalog, output)
    assert (root / "catalog.json").read_bytes() == before


def test_different_note_directories_cannot_share_an_index(tmp_path):
    catalog, _folder = source(tmp_path)
    other_catalog, other_folder = source(tmp_path / "other")
    output = tmp_path / "output"
    import_catalog(catalog, output)
    before = (library_root(output) / "catalog.json").read_bytes()
    with pytest.raises(ValueError, match="不能合并不同笔记输出目录"):
        import_catalog(other_catalog, output)
    assert (library_root(output) / "catalog.json").read_bytes() == before
    assert other_folder.is_dir()


def test_requested_directory_filters_catalog_and_keeps_batches_separate(tmp_path):
    catalog, folder = source(tmp_path)
    doc = json.loads(catalog.read_text())
    import copy
    import shutil

    second = copy.deepcopy(doc["batches"][0])
    second["id"] = "batch-002"
    destination = folder.parents[1] / "batch-002/another-lesson"
    shutil.copytree(folder, destination)
    video = second["videos"][0]
    video["asset_id"] = "v_2"
    video["reviewed_note"] = str(destination / "notes.md")
    video["codex_study"]["status_file"] = str(destination / "status.json")
    doc["batches"].append(second)
    write_json(catalog, doc)
    output = tmp_path / "output"
    selected = folder.parent
    result = import_catalog(catalog, output, notes_directory=selected)
    assert result["batches"] == {"batch-001": 1}
    c = read_catalog(library_root(output))
    assert len(c["entries"]) == 1
    assert str(selected) in (library_root(output) / "index.html").read_text()
    with pytest.raises(ValueError, match="不能合并不同笔记输出目录"):
        import_catalog(catalog, output, notes_directory=destination.parent)
    separate = import_catalog(catalog, tmp_path / "separate-output", notes_directory=destination.parent)
    assert separate["batches"] == {"batch-002": 1}


def test_legacy_mixed_index_requires_split_before_import_or_reuse(tmp_path):
    from video_tutor.study_library import render_index

    catalog, _folder = source(tmp_path)
    output = tmp_path / "output"
    import_catalog(catalog, output)
    root = library_root(output)
    c = read_catalog(root)
    c.pop("notes_directory")
    c["entries"].append({"entry_id": "synthetic", "kind": "workflow_snapshot"})
    write_json(root / "catalog.json", c)
    before = (root / "catalog.json").read_bytes()
    with pytest.raises(ValueError, match="不同笔记输出目录"):
        import_catalog(catalog, output)
    with pytest.raises(ValueError, match="不同笔记输出目录"):
        previous_notes(output)
    with pytest.raises(ValueError, match="不同笔记输出目录"):
        render_index(root, c)
    assert (root / "catalog.json").read_bytes() == before


def test_workflow_directory_and_historical_directory_are_not_interchangeable(tmp_path):
    from video_tutor.study_library import register_batch, snapshot_run

    catalog, _folder = source(tmp_path)
    historical = tmp_path / "historical-output"
    import_catalog(catalog, historical)
    before = (library_root(historical) / "catalog.json").read_bytes()
    with pytest.raises(ValueError, match="不能合并不同笔记输出目录"):
        register_batch(historical, {"id": "new-batch"}, {})
    with pytest.raises(ValueError, match="不能合并不同笔记输出目录"):
        snapshot_run(historical, "unused-run")
    assert (library_root(historical) / "catalog.json").read_bytes() == before
    workflow = tmp_path / "workflow-output"
    register_batch(workflow, {"id": "first-batch"}, {})
    with pytest.raises(ValueError, match="不能合并不同笔记输出目录"):
        import_catalog(catalog, workflow)

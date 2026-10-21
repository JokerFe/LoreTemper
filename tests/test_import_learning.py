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

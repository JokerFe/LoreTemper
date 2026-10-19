import pytest
from PIL import Image
from video_tutor.keyframes import keyframe_plan
from video_tutor.pipeline import frame_groups


def frames(tmp_path, values):
    result = []
    for i, value in enumerate(values):
        p = tmp_path / f"{i}.png"
        Image.new("RGB", (320, 180), (value,) * 3).save(p)
        result.append({"id": f"f_{i}", "time_ms": i * 1000, "path": str(p)})
    return result


def test_keeps_endpoints_and_brief_scene_change(tmp_path):
    source = frames(tmp_path, [20, 20, 20, 220, 20, 20, 20, 20, 20, 20])
    speech = [{"start_ms": 0, "end_ms": 30000, "raw_text": "讲解"}]
    plan = keyframe_plan(frame_groups(source, 4), speech)
    chosen = [f["id"] for _, group in plan for f in group]
    assert chosen == ["f_0", "f_2", "f_3", "f_4", "f_9"]
    assert all(len(group) <= 4 for _, group in plan)
    assert len(speech) == 1  # Sampling never rewrites or drops speech.


def test_silent_demonstration_keeps_every_frame(tmp_path):
    source = frames(tmp_path, [20] * 65)
    plan = keyframe_plan(frame_groups(source, 4), [])
    assert [f for _, group in plan for f in group] == source


def test_preserves_completed_group_keys_and_covers_each_window(tmp_path):
    source = frames(tmp_path, [20] * 95)
    groups = frame_groups(source, 4)
    plan = keyframe_plan(groups, [{"start_ms": 0, "end_ms": 95000}], preserve_groups=2)
    assert plan[:2] == [("00000", groups[0]), ("00001", groups[1])]
    chosen = [f for _, group in plan for f in group]
    assert len({f["id"] for f in chosen}) == len(chosen)
    assert {f["time_ms"] // 30000 for f in chosen} == {0, 1, 2, 3}
    assert chosen[-1] == source[-1]
    assert len(plan) < len(groups) / 2
    assert len({key for key, _ in plan}) == len(plan)


def test_planning_can_be_interrupted(tmp_path):
    def stop():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        keyframe_plan(frame_groups(frames(tmp_path, [20] * 10), 4), [], check=stop)

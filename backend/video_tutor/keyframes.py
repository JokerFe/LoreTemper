"""Optional course-outline sampling; original frames and full speech stay intact."""

from collections import defaultdict

import numpy as np
from PIL import Image


def keyframe_plan(groups, transcript, size=4, preserve_groups=0, check=lambda: None):
    """Keep completed groups verbatim, then cover every remaining 30-second window.

    Speech-free windows retain all frames. Else keep both endpoints, the strongest
    adjacent change pair, and all large scene transitions. This is intentionally
    an outline mode, not a guarantee of capturing every brief visual operation.
    """
    if not 0 <= preserve_groups <= len(groups):
        raise ValueError("已完成画面组范围无效")
    plan = [(f"{i:05}", group) for i, group in enumerate(groups[:preserve_groups])]
    windows = defaultdict(list)
    for group in groups[preserve_groups:]:
        for frame in group:
            windows[frame["time_ms"] // 30000].append(frame)
    for window, frames in sorted(windows.items()):
        check()
        has_speech = any(t["end_ms"] > window * 30000 and t["start_ms"] < (window + 1) * 30000 for t in transcript)
        chosen = list(range(len(frames)))
        if has_speech and len(frames) > 4:
            changes = []
            previous = None
            for i, frame in enumerate(frames):
                check()
                with Image.open(frame["path"]) as im:
                    current = np.asarray(im.convert("L").resize((320, 180)), dtype=np.float32) / 255
                if previous is not None:
                    delta = np.abs(current - previous)
                    changes.append((float(delta.mean()), i))
                previous = current
            strongest = max(changes)[1]
            keep = {0, len(frames) - 1, strongest - 1, strongest}
            for score, i in changes:
                if score >= 0.12:
                    keep.update((i - 1, i))
            chosen = sorted(keep)
        selected = [frames[i] for i in chosen]
        for i in range(0, len(selected), size):
            plan.append((f"key-v1-{window:06}-{i // size:03}", selected[i : i + size]))
    return plan

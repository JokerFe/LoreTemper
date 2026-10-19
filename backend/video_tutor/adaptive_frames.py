"""Colour-aware state deduplication with local-change and evidence anchors.

Inspired by the published approach of claude-real-video (MIT); independently
implemented here. No upstream source is copied. Decisions retain source IDs and
timestamps. A discarded frame remains available in the source and decision log.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class FramePolicy:
    width: int = 384
    height: int = 216
    global_delta: float = .035
    local_delta: float = .16
    scene_delta: float = .18
    history: int = 4
    # Periodic visual context bounds gaps, but does not claim exhaustive coverage.
    max_gap_ms: int = 90000


def signature(path, policy):
    with Image.open(path) as im:
        return np.asarray(im.convert('RGB').resize((policy.width, policy.height)), dtype=np.float32) / 255


def difference(a, b):
    # RGB max-channel difference preserves similar-luminance colour changes.
    delta = np.abs(a - b).max(axis=2)
    h, w = delta.shape
    # 16x9 tiles: a small parameter/palette panel can matter despite a tiny mean.
    tiles = delta.reshape(9, h // 9, 16, w // 16).mean(axis=(1, 3))
    return float(delta.mean()), float(tiles.max())


def select_adaptive_frames(frames, anchors=(), policy=None, check=lambda: None):
    policy = policy or FramePolicy()
    if policy.width % 16 or policy.height % 9 or policy.history < 1:
        raise ValueError('Invalid frame policy')
    ordered = sorted(frames, key=lambda f: f['time_ms'])
    if not ordered:
        return [], {'policy': asdict(policy), 'decisions': [], 'anchors': []}
    kept, arrays, decisions = [], [], []
    previous = None
    for frame in ordered:
        check()
        current = signature(frame['path'], policy)
        reasons, match = [], None
        distances = [difference(current, other) for other in arrays[-policy.history:]]
        equivalent = [i for i, (g, local) in enumerate(distances)
                      if g < policy.global_delta and local < policy.local_delta]
        if not kept:
            reasons.append('first')
        elif not equivalent:
            reasons.append('new_colour_or_local_state')
        elif frame['time_ms'] - kept[-1]['time_ms'] >= policy.max_gap_ms:
            reasons.append('context_gap')
        else:
            match = kept[max(0, len(kept) - policy.history) + equivalent[-1]]['id']
        if previous is not None and difference(current, previous)[0] >= policy.scene_delta:
            reasons.append('scene_transition')
        if reasons:
            kept.append(frame)
            arrays.append(current)
        decisions.append({'id': frame['id'], 'time_ms': frame['time_ms'],
                          'reasons': reasons, 'equivalent_to': match,
                          'distance_to_last_kept': distances[-1] if distances else None})
        previous = current
    selected = {f['id']: f for f in kept}
    by_id = {d['id']: d for d in decisions}
    anchor_log = []
    # Explicit speech-derived evidence requests bypass perceptual deduplication.
    for anchor in anchors:
        at = anchor['time_ms']
        radius = anchor.get('radius_ms', 6000)
        local = [f for f in ordered if abs(f['time_ms'] - at) <= radius]
        if not local:
            local = [min(ordered, key=lambda f: abs(f['time_ms'] - at))]
        # Prefer close source time. Sharpness alone can pick an unrelated dialog.
        chosen = min(local, key=lambda f: abs(f['time_ms'] - at))
        selected[chosen['id']] = chosen
        by_id[chosen['id']]['reasons'].append('speech_evidence_anchor')
        anchor_log.append({**anchor, 'frame_id': chosen['id'],
                           'actual_time_ms': chosen['time_ms']})
    last = ordered[-1]
    selected[last['id']] = last
    by_id[last['id']]['reasons'].append('last')
    manifest = {'version': 'adaptive-rgb-v1', 'policy': asdict(policy),
                'source_count': len(ordered), 'selected_count': len(selected),
                'decisions': decisions, 'anchors': anchor_log}
    return sorted(selected.values(), key=lambda f: f['time_ms']), manifest


def evidence_fingerprint(frames, transcript, policy):
    import json
    digest = hashlib.sha256(json.dumps({'transcript': transcript, 'policy': asdict(policy)},
                                      sort_keys=True, ensure_ascii=False).encode())
    for frame in frames:
        digest.update(str((frame['id'], frame['time_ms'])).encode())
        digest.update(Path(frame['path']).read_bytes())
    return digest.hexdigest()

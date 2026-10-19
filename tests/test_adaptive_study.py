import json

import pytest
from PIL import Image, ImageDraw
from video_tutor.adaptive_frames import select_adaptive_frames
from video_tutor.study import source_text
from video_tutor.thematic_study import (
    compact_speech,
    make_overviews,
    parse_json,
    validate_image_references,
    validate_plan,
)


def sources(tmp_path, images, times=None):
    result = []
    for i, image in enumerate(images):
        path = tmp_path / f'{i}.png'
        image.save(path)
        result.append({'id': f'f_{i}', 'time_ms': times[i] if times else i * 1000,
                       'path': str(path)})
    return result


def test_static_frames_reduce_but_source_times_and_anchors_survive(tmp_path):
    frames = sources(tmp_path, [Image.new('RGB', (384, 216), 'white')] * 8,
                     [1000, 9000, 13000, 21000, 40000, 80000, 95000, 100000])
    before = json.dumps(frames)
    selected, manifest = select_adaptive_frames(frames, [{'time_ms': 21000, 'reason': 'parameter'}])
    assert {f['time_ms'] for f in selected} == {1000, 21000, 95000, 100000}
    assert json.dumps(frames) == before
    assert manifest['anchors'][0]['frame_id'] == 'f_3'
    assert len(manifest['decisions']) == len(frames)


def test_equal_luminance_colour_and_small_local_change_are_retained(tmp_path):
    a = Image.new('RGB', (384, 216), (255, 0, 0))
    b = Image.new('RGB', (384, 216), (0, 130, 0))
    # Almost equal grayscale: RGB comparison must distinguish them.
    assert abs(a.convert('L').getpixel((0, 0)) - b.convert('L').getpixel((0, 0))) <= 1
    c = b.copy()
    ImageDraw.Draw(c).rectangle((48, 48, 71, 71), fill='white')
    frames = sources(tmp_path, [a, b, c, c])
    selected, _ = select_adaptive_frames(frames)
    assert {f['id'] for f in selected} >= {'f_0', 'f_1', 'f_2'}


def test_short_scene_is_not_removed_and_overview_includes_duplicates(tmp_path):
    frames = sources(tmp_path, [Image.new('RGB', (384, 216), x) for x in ['white', 'black', 'white', 'white']])
    selected, manifest = select_adaptive_frames(frames)
    assert 'f_1' in {f['id'] for f in selected}
    assert len(manifest['decisions']) == 4
    sheets = make_overviews(frames, tmp_path / 'sheets')
    assert len(sheets) == 1
    with Image.open(sheets[0]['path']) as im:
        assert im.size == (1920, 1616)


def test_visual_plan_rejects_gaps_invalid_indices_and_empty_evidence():
    frames = [{'id': 'f_0'}]
    good = {'sections': [{'start_ms': 0, 'end_ms': 1000,
                         'visual_requests': [{'frame_index': 0, 'purpose': 'compare'}]}]}
    assert validate_plan(good, frames, 0, 1000)
    for change in ('gap', 'invalid_index', 'no_evidence'):
        plan = json.loads(json.dumps(good))
        if change == 'gap':
            plan['sections'][0]['start_ms'] = 1
        elif change == 'invalid_index':
            plan['sections'][0]['visual_requests'][0]['frame_index'] = -1
        else:
            plan['sections'][0]['visual_requests'] = []
        with pytest.raises(ValueError):
            validate_plan(plan, frames, 0, 1000)


def test_topic_boundaries_preserve_overlapping_speech():
    transcript = [{'start_ms': 900, 'end_ms': 1100, 'raw_text': '关键条件'},
                  {'start_ms': 1100, 'end_ms': 2000, 'raw_text': '必要参数'}]
    assert '关键条件' in source_text(transcript, 0, 1000)
    assert '关键条件' in source_text(transcript, 1000, 2000)
    assert '必要参数' in source_text(transcript, 1000, 2000)
    compact = compact_speech(transcript, 1000, 2000)
    assert '关键条件 必要参数' in compact
    assert '00:00.900' in compact and '00:02.000' in compact


def test_visual_plan_accepts_verbal_sections_without_dropping_time_coverage():
    plan = {'sections': [
        {'start_ms': 0, 'end_ms': 200, 'visual_requests': []},
        {'start_ms': 200, 'end_ms': 700,
         'visual_requests': [{'frame_index': 0, 'purpose': 'page comparison'}]},
        {'start_ms': 700, 'end_ms': 1000, 'visual_requests': []},
    ]}
    assert validate_plan(plan, [{'id': 'f_0'}], 0, 1000) == plan['sections']
    plan['sections'][1]['start_ms'] = 201
    with pytest.raises(ValueError, match='contiguous'):
        validate_plan(plan, [{'id': 'f_0'}], 0, 1000)


def test_json_recovery_only_closes_complete_values():
    assert parse_json('{"sections":[{"title":"a } [ \\"quoted\\""}') == {
        'sections': [{'title': 'a } [ "quoted"'}]}
    for text in ('{"title":"incomplete', '{"end_ms":', '{"items":[1,', '{"x":tru', '{"x":1]'):
        with pytest.raises(json.JSONDecodeError):
            parse_json(text)


def test_cancellation_propagates(tmp_path):
    def stop():
        raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        select_adaptive_frames(sources(tmp_path, [Image.new('RGB', (384, 216))]), check=stop)


def test_citations_require_real_selected_source_images(tmp_path):
    (tmp_path / 'images').mkdir()
    (tmp_path / 'images' / 'f_1.jpg').write_bytes(b'fixture')
    validate_image_references('![example](images/f_1.jpg)', tmp_path, {'f_1'})
    for link in ('images/f_2.jpg', 'images/../f_1.jpg'):
        with pytest.raises(ValueError):
            validate_image_references(f'![example]({link})', tmp_path, {'f_1'})

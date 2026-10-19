from video_tutor.study import valid_response


def test_empty_gap_audit_is_a_successful_response():
    value = '{"visual_gaps":[],"content_gaps":[],"noncritical_unknowns":[]}'
    assert valid_response('visual-gap-audit', value)
    assert valid_response('visual-gap-audit', '```json\n' + value + '\n```')


def test_invalid_gap_audits_do_not_pass_length_check():
    for value in ['{}', '{"visual_gaps":[]}', '{"visual_gaps":[],"content_gaps":[],"noncritical_unknowns":null}', 'x' * 200, '{"visual_gaps":[]']:
        assert not valid_response('visual-gap-audit', value)


def test_short_prose_still_rejected():
    assert not valid_response('course-summary', 'done')
    assert valid_response('course-summary', '完整笔记' * 40)

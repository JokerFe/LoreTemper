import json
import httpx
import pytest
from PIL import Image
from video_tutor.config import Settings
from video_tutor.llm import LocalLLM, OutputTruncated, constrained_schema, validate_output
from video_tutor.schemas import Observation


def client(monkeypatch, tmp_path, responses, check=lambda: None, **settings):
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        response = responses[len(calls) - 1]
        return httpx.Response(
            response if isinstance(response, int) else 200, json={} if isinstance(response, int) else response
        )

    transport = httpx.MockTransport(handle)
    real_client = httpx.Client
    monkeypatch.setattr("video_tutor.llm.httpx.Client", lambda **kw: real_client(transport=transport, **kw))
    monkeypatch.setattr("video_tutor.llm.time.sleep", lambda _: None)
    llm = LocalLLM(Settings(**settings), check, [])
    llm.info["test"] = {"capabilities": ["vision", "thinking"]}
    path = tmp_path / "frame.png"
    Image.new("RGB", (10, 10)).save(path)
    frames = [{"id": "f_0", "time_ms": 0, "path": str(path)}]
    return llm, calls, frames


def response(content='{"description":"端口 3000","frame_ids":["f_0"]}', reason="stop", prompt_tokens=3000):
    return {"message": {"content": content}, "done_reason": reason, "prompt_eval_count": prompt_tokens}


def test_truncation_retries_with_room_and_preserves_images(monkeypatch, tmp_path):
    llm, calls, frames = client(monkeypatch, tmp_path, [response('{"description":', "length"), response()])
    result = llm.chat("test", Observation, "观察画面", {}, frames)
    assert result["description"] == "端口 3000"
    assert [c["options"]["num_predict"] for c in calls] == [2048, 4096]
    assert calls[0]["messages"][1] == calls[1]["messages"][1]
    assert calls[1]["think"] is False
    assert calls[1]["format"] == "json"
    assert llm.metrics[0]["done_reason"] == "length"


def test_persistent_truncation_is_bounded_and_never_accepted(monkeypatch, tmp_path):
    llm, calls, frames = client(monkeypatch, tmp_path, [response(reason="length", prompt_tokens=6000)] * 3)
    with pytest.raises(OutputTruncated):
        llm.chat("test", Observation, "观察", {}, frames)
    assert len(calls) == 3
    assert calls[1]["options"]["num_predict"] == 1680
    assert len(calls[2]["messages"]) == len(calls[1]["messages"])


def test_invalid_json_then_truncated_then_valid(monkeypatch, tmp_path):
    llm, calls, frames = client(monkeypatch, tmp_path, [response("invalid"), response(reason="length"), response()])
    assert llm.chat("test", Observation, "观察", {}, frames)["frame_ids"] == ["f_0"]
    assert len(calls) == 3


@pytest.mark.parametrize("status, count", [(400, 1), (401, 1), (429, 3), (503, 3)])
def test_http_retry_policy(monkeypatch, tmp_path, status, count):
    llm, calls, frames = client(monkeypatch, tmp_path, [status] * 3)
    with pytest.raises(RuntimeError):
        llm.chat("test", Observation, "观察", {}, frames)
    assert len(calls) == count


def test_retry_backoff_remains_cancellable(monkeypatch, tmp_path):
    class Cancelled(Exception):
        pass

    checks = 0

    def check():
        nonlocal checks
        checks += 1
        if checks == 3:
            raise Cancelled()

    llm, calls, frames = client(monkeypatch, tmp_path, [503] * 3, check)
    with pytest.raises(Cancelled):
        llm.chat("test", Observation, "观察", {}, frames)
    assert len(calls) == 1


def test_generation_schema_bounds_repeated_evidence_without_mutating_source():
    original = Observation.model_json_schema()
    bounded = constrained_schema(original, {"f_0", "f_1"})
    assert bounded["properties"]["frame_ids"]["maxItems"] == 2
    assert bounded["properties"]["visible_texts"]["maxItems"] == 24
    assert "maxItems" not in original["properties"]["frame_ids"]


def test_invalid_reference_is_retried_even_in_json_mode(monkeypatch, tmp_path):
    llm, calls, frames = client(
        monkeypatch,
        tmp_path,
        [
            response(reason="length"),
            response('{"description":"bad reference","frame_ids":["invented"]}'),
            response(),
        ],
    )
    assert llm.chat("test", Observation, "观察", {}, frames)["frame_ids"] == ["f_0"]
    assert len(calls) == 3


@pytest.mark.parametrize("start,end", [(100, 99), (-1, 10), (0, 101)])
def test_time_bounds_are_checked_without_schema_decoding(start, end):
    with pytest.raises(ValueError):
        validate_output({"steps": [{"start_ms": start, "end_ms": end}]}, set(), (0, 100))


def test_thinking_only_vision_failure_is_actionable_and_not_retried(monkeypatch, tmp_path):
    result = response("", "length")
    result["message"]["thinking"] = "synthetic thinking-only fixture"
    llm, calls, frames = client(monkeypatch, tmp_path, [result])
    with pytest.raises(RuntimeError, match="Instruct"):
        llm.chat("test", Observation, "观察", {}, frames)
    assert len(calls) == 1
    assert llm.metrics[0]["thinking_chars"] > 0
    assert llm.metrics[0]["content_chars"] == 0


def test_default_vision_model_is_explicitly_instruct():
    assert Settings().vision_model == "qwen3-vl:8b-instruct"

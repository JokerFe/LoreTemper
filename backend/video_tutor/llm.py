from __future__ import annotations
import base64
import io
import json
import time
import httpx
from PIL import Image
from .config import ROOT

GENERATION_POLICY_VERSION = 3


class OutputTruncated(RuntimeError):
    """No complete result was produced within the bounded output/context budget."""


def prompt(name):
    return (ROOT / "prompts" / f"{name}.txt").read_text()


def model_inventory(settings):
    with httpx.Client(base_url=settings.ollama_url, timeout=10) as c:
        r = c.get("/api/tags")
        r.raise_for_status()
        return r.json().get("models", [])


def verify_model(settings, name, vision=False):
    with httpx.Client(base_url=settings.ollama_url, timeout=30) as c:
        r = c.post("/api/show", json={"model": name})
        r.raise_for_status()
        info = r.json()
    if info.get("remote_model") or info.get("remote_host"):
        raise RuntimeError("不允许远端或云模型")
    if vision and "vision" not in info.get("capabilities", []):
        raise RuntimeError(f"{name} 不具备图片输入能力")
    return info


def encode_image(path, max_edge):
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_edge, max_edge))
        b = io.BytesIO()
        im.save(b, format="JPEG", quality=90)
    return base64.b64encode(b.getvalue()).decode()


def constrained_schema(schema, evidence_ids, bounds=None):
    result = json.loads(json.dumps(schema))
    allowed = sorted(set(evidence_ids))

    def walk(node):
        if isinstance(node, dict):
            for name, prop in node.get("properties", {}).items():
                # Bound generation itself: otherwise a valid JSON array can repeat
                # indefinitely until Ollama exhausts num_predict.
                if prop.get("type") == "array":
                    prop["maxItems"] = 24 if name == "visible_texts" else 16
                if prop.get("type") == "string":
                    prop["maxLength"] = 120 if name == "title" else 800
                if allowed and len(allowed) <= 128:
                    if name in ("evidence_ids", "frame_ids", "summary_evidence_ids"):
                        prop["items"] = {"type": "string", "enum": allowed}
                        prop["maxItems"] = min(len(allowed), 16)
                    elif name == "frame_id":
                        prop["enum"] = allowed
                if bounds and name in ("start_ms", "end_ms"):
                    prop["minimum"], prop["maximum"] = bounds
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(result)
    return result


def collect_evidence(value):
    result = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("evidence_ids", "frame_ids", "summary_evidence_ids", "allowed_evidence_ids"):
                result.update(child)
            elif key == "frame_id":
                result.add(child)
            elif key == "id" and isinstance(child, str) and child.startswith("t_"):
                result.add(child)
            else:
                result.update(collect_evidence(child))
    elif isinstance(value, list):
        for child in value:
            result.update(collect_evidence(child))
    return result


def validate_output(value, allowed, bounds=None):
    unknown = collect_evidence(value) - allowed
    if unknown:
        raise ValueError("模型引用了不存在的证据：" + ", ".join(sorted(unknown)))

    def walk(node):
        if isinstance(node, dict):
            if "start_ms" in node and "end_ms" in node and node["start_ms"] > node["end_ms"]:
                raise ValueError("模型步骤结束时间早于开始时间")
            for key, child in node.items():
                if bounds and key in ("start_ms", "end_ms") and not bounds[0] <= child <= bounds[1]:
                    raise ValueError("模型步骤时间超出所在片段")
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)


class LocalLLM:
    def __init__(self, settings, check, metrics):
        self.settings = settings
        self.check = check
        self.metrics = metrics
        self.loaded = set()
        self.info = {}

    def chat(self, model, schema, system, payload, frames=None):
        self.check()
        if model not in self.info:
            self.info[model] = verify_model(self.settings, model, bool(frames))
        allowed = {f["id"] for f in frames} if frames else collect_evidence(payload)
        bounds = (payload["start_ms"], payload["end_ms"]) if "start_ms" in payload and "end_ms" in payload else None
        output_schema = constrained_schema(schema.model_json_schema(), allowed, bounds)
        content = json.dumps(payload, ensure_ascii=False)
        messages = [
            {
                "role": "system",
                "content": system
                + "\n输出要简洁，不重复描述或重复引用。同一画面的同一文字只记录一次。"
                + "\nJSON Schema:\n"
                + json.dumps(output_schema, ensure_ascii=False, separators=(",", ":")),
            }
        ]
        for f in frames or []:
            messages.append(
                {
                    "role": "user",
                    "content": f"frame_id={f['id']}, time_ms={f['time_ms']}",
                    "images": [encode_image(f["path"], self.settings.image_max_edge)],
                }
            )
        messages.append({"role": "user", "content": content})
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "format": output_schema,
            "keep_alive": "5m",
            "options": {
                "temperature": 0,
                "num_ctx": self.settings.vision_num_ctx if frames else self.settings.text_num_ctx,
                "num_predict": 2048 if frames else 4096,
            },
        }
        if "thinking" in self.info[model].get("capabilities", []):
            body["think"] = False
        self.loaded.add(model)
        original_messages = list(messages)
        last = None
        for attempt in range(3):
            self.check()
            started = time.monotonic()
            try:
                with httpx.Client(timeout=httpx.Timeout(self.settings.request_timeout_seconds, connect=10)) as c:
                    response = c.post(self.settings.ollama_url + "/api/chat", json=body)
                    response.raise_for_status()
                    result = response.json()
                self.metrics.append(
                    {
                        "model": model,
                        "seconds": time.monotonic() - started,
                        "images": len(frames or []),
                        "attempt": attempt + 1,
                        "num_predict": body["options"]["num_predict"],
                        "thinking_chars": len(result.get("message", {}).get("thinking") or ""),
                        "content_chars": len(result.get("message", {}).get("content") or ""),
                        **{
                            k: result.get(k)
                            for k in ("load_duration", "prompt_eval_count", "eval_count", "done_reason")
                        },
                    }
                )
                self.check()
                if result.get("done_reason") == "length":
                    if frames and result.get("message", {}).get("thinking") and not result["message"].get("content"):
                        raise RuntimeError(
                            "视觉模型仅生成思考内容，未生成分析结果；当前模型未遵循关闭思考设置。"
                            "请在偏好设置改用本地 Instruct 视觉模型（如 qwen3-vl:8b-instruct），然后重新分析。"
                        )
                    # Keep the input intact, leave space for the corrective prompt,
                    # and grow only the output budget, never the model's context.
                    room = max(1, body["options"]["num_ctx"] - result.get("prompt_eval_count", 0) - 512)
                    ceiling = 6144 if frames else 8192
                    body["options"]["num_predict"] = min(body["options"]["num_predict"] * 2, ceiling, room)
                    raise OutputTruncated("模型输出被截断，已尝试缩短回答并按上下文余量调整输出预算")
                parsed = schema.model_validate_json(result["message"]["content"])
                value = parsed.model_dump()
                validate_output(value, allowed, bounds)
                return value
            except (httpx.HTTPError, ValueError, KeyError, OutputTruncated) as e:
                last = e
                if attempt == 2:
                    break
                if isinstance(e, (ValueError, KeyError, OutputTruncated)):
                    if isinstance(e, OutputTruncated):
                        # Escape repetitive schema-constrained decoding while still
                        # requiring JSON and validating its structure/references here.
                        body["format"] = "json"
                    # Replace the repair instruction instead of growing history;
                    # never parse, complete, or commit a truncated JSON response.
                    body["messages"] = original_messages + [
                        {
                            "role": "user",
                            "content": "上一次输出未完成或校验失败："
                            + str(e)[:600]
                            + "。请从头生成完整 JSON；简短描述，合并重复项，只保留重要文字和证据，不要续写残缺结果。",
                        }
                    ]
                else:
                    if (
                        isinstance(e, httpx.HTTPStatusError)
                        and e.response.status_code not in (408, 429)
                        and e.response.status_code < 500
                    ):
                        break
                    for _ in range(20 * (attempt + 1)):
                        self.check()
                        time.sleep(0.1)
        if isinstance(last, OutputTruncated):
            raise last
        raise RuntimeError(f"本地模型处理失败：{str(last)[:800]}")

    def unload(self, model=None):
        for name in list(self.loaded) if model is None else [model]:
            try:
                with httpx.Client(timeout=30) as c:
                    c.post(self.settings.ollama_url + "/api/generate", json={"model": name, "keep_alive": 0})
            except httpx.HTTPError:
                pass
            self.loaded.discard(name)

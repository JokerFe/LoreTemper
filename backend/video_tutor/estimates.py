"""API-equivalent estimates, explicitly not subscription quota multipliers."""

from __future__ import annotations

PRICING_SOURCE = "https://developers.openai.com/api/docs/pricing"
PRICING_CHECKED = "2026-10-19"
# Standard, short-context USD per million. Unknown names deliberately remain unknown.
RATES = {
    "gpt-6.1-sol": (2, 0.1, 10),
    "gpt-6-astra": (10, 1, 50),
    "gpt-6-sol": (2, 0.2, 10),
    "gpt-6-luna": (0.1, 0.01, 0.5),
    "gpt-5.6-sol": (4, 0.4, 20),
    "gpt-5.6-terra": (2, 0.2, 12),
    "gpt-5.6-luna": (0.2, 0.02, 1.2),
}
# Time-weighted completed call receipts for 7 courses / 855.37 video minutes.
BASE_USAGE = {"input_tokens": 597526, "cached_input_tokens": 212746, "output_tokens": 34717}


def equivalent_cost(usage, model, tier="default"):
    rates = RATES.get(model)
    if rates is None:
        return None
    i, c, o = (usage.get(k, 0) for k in ("input_tokens", "cached_input_tokens", "output_tokens"))
    return ((max(0, i - c) * rates[0] + c * rates[1] + o * rates[2]) / 1e6) * (2 if tier == "priority" else 1)


def cost_multiplier(model, tier="default"):
    value = equivalent_cost(BASE_USAGE, model, tier)
    return round(value / equivalent_cost(BASE_USAGE, "gpt-6.1-sol"), 2) if value is not None else None


def estimate(duration_ms, provider, model, tier="default", reasoning="medium"):
    factor = duration_ms / 3600000
    usage = {k: round(v * factor) for k, v in BASE_USAGE.items()}
    value = equivalent_cost(usage, model, tier) if provider == "codex" else 0
    return {
        "usage": usage if provider == "codex" else None,
        "total_tokens": usage["input_tokens"] + usage["output_tokens"] if provider == "codex" else None,
        "api_equivalent_usd": round(value, 3) if value is not None else None,
        "multiplier": cost_multiplier(model, tier) if provider == "codex" else 0,
        "baseline_model": "gpt-6.1-sol",
        "reasoning": reasoning,
        "pricing_source": PRICING_SOURCE,
        "pricing_checked": PRICING_CHECKED,
        "basis": "7门课程855分钟的调用回执，按时长加权；输入包含缓存，输出包含推理",
        "caveat": "倍率仅比较相同token结构的API等值费用，不代表订阅额度。切换模型或思考档位后的token变化尚未实测；主对话整理及重试可能增加消耗。"
        if provider == "codex"
        else "Ollama在本机运行，无模型API费用；电费、设备成本和本地token未估算。",
    }

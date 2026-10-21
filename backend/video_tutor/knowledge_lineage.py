"""Account for every retained knowledge item across notes, rules and capabilities."""

from __future__ import annotations

import html
import json
from pathlib import Path

from pydantic import Field

from .config import atomic_write
from .learning_quality import SOURCE_POLICY, Strict, compact, validated_call
from .study import write_json


class Destination(Strict):
    knowledge_id: str
    rule_ids: list[str]
    note_quote: str
    reason: str = Field(min_length=1)


class Destinations(Strict):
    items: list[Destination]


def chunks(values, limit):
    batch, size = [], 0
    for value in values:
        length = len(json.dumps(value, ensure_ascii=False))
        if length > limit:
            raise ValueError("单条知识或规则超出核对预算；已保留材料，不截断知识")
        if batch and size + length > limit:
            yield batch
            batch, size = [], 0
        batch.append(value)
        size += length
    if batch:
        yield batch


def map_knowledge(runner, package, notes, rules, config):
    coverage_root = package / "references/coverage"
    coverage_root.mkdir(parents=True, exist_ok=True)
    routes = {r["rule_id"]: r for r in json.loads((coverage_root / "rules.json").read_text())}
    rows, omissions = [], []
    for n, note in enumerate(notes, 1):
        source_id = f"S{n:03}"
        if not note.get("knowledge_path"):
            omissions.append({"source_id": source_id, "reason": "输入笔记没有附带原学习知识清单，无法证明视频知识覆盖"})
            continue
        knowledge = json.loads(Path(note["knowledge_path"]).read_text())["items"]
        if not knowledge:
            raise ValueError("串联学习缺少知识清单，已保留笔记，请补齐后创建新任务")
        write_json(package / f"references/sources/source-{n:03}/knowledge.json", {"items": knowledge})
        text = Path(note["path"]).read_text()
        source_rules = [
            {k: r[k] for k in ["id", "claim", "rationale", "method", "conditions", "exceptions", "quote"]}
            for r in rules
            if r["source_id"] == source_id
        ]
        allowed = {r["id"] for r in source_rules}
        accumulated = {k["id"]: {"knowledge": k, "rule_ids": set(), "quotes": [], "reasons": []} for k in knowledge}
        # Every source character and every candidate rule is examined in bounded windows.
        # No first-N retrieval shortcut that could silently discard a destination.
        for ki, group in enumerate(chunks(knowledge, 3500)):
            ids = {k["id"] for k in group}
            for ri, rgroup in enumerate(list(chunks(source_rules, 3500)) or [[]]):
                rule_ids = {r["id"] for r in rgroup}
                for ti, offset in enumerate(range(0, max(1, len(text)), 4000)):
                    excerpt = text[offset : offset + 4000]

                    def validate(raw, ids=ids, rule_ids=rule_ids, excerpt=excerpt):
                        result = Destinations.model_validate_json(
                            raw.strip().removeprefix("```json\n").removesuffix("```")
                        ).model_dump()["items"]
                        returned = [r["knowledge_id"] for r in result]
                        if len(returned) != len(ids) or set(returned) != ids:
                            raise ValueError("知识点去向必须完整覆盖本组ID且每项一次")
                        for item in result:
                            if (
                                len(set(item["rule_ids"])) != len(item["rule_ids"])
                                or not set(item["rule_ids"]) <= rule_ids
                            ):
                                raise ValueError("知识点关联了非本来源或不存在的规则")
                            if item["note_quote"] and (
                                len(compact(item["note_quote"])) < 4
                                or compact(item["note_quote"]) not in compact(excerpt)
                            ):
                                raise ValueError("知识点对应的笔记短句不在本段原文中")
                        return result

                    prompt = (
                        SOURCE_POLICY
                        + """逐项追踪学习知识的去向，不要求所有知识都变成规则。
只比较本次给出的笔记窗口和候选规则。保留适用条件，语义没有覆盖就不要关联。
输出JSON：{"items":[{"knowledge_id":"原ID","rule_ids":["同来源规则ID"],"note_quote":"该知识在当前笔记窗口中的连续原文短句，未覆盖则为空字符串","reason":"说明关联依据或未关联原因；指出仅为案例、条件缺失或待核查等"}]}。
所有知识ID必须且只能出现一次，规则ID仅限本次提供的ID。不要把没在本窗口找到当成整个来源缺失。\n知识：\n"""
                    )
                    prompt += (
                        json.dumps(group, ensure_ascii=False)
                        + "\n候选规则：\n"
                        + json.dumps(rgroup, ensure_ascii=False)
                        + "\n笔记窗口：\n"
                        + excerpt
                    )
                    result = validated_call(runner, f"knowledge-destinations-{n}-{ki}-{ri}-{ti}", prompt, validate)
                    for item in result:
                        acc = accumulated[item["knowledge_id"]]
                        acc["rule_ids"].update(item["rule_ids"])
                        if item["note_quote"]:
                            acc["quotes"].append(item["note_quote"])
                        acc["reasons"].append(item["reason"])
        for item in accumulated.values():
            rule_ids = sorted(item["rule_ids"])
            assert set(rule_ids) <= allowed
            status = (
                "incorporated"
                if rule_ids and item["quotes"]
                else "retained_in_notes"
                if item["quotes"]
                else "needs_review"
            )
            rows.append(
                {
                    "knowledge_id": item["knowledge"]["id"],
                    "source_id": source_id,
                    "source_identity": note["source_identity"],
                    "run_id": note.get("run_id"),
                    "knowledge": item["knowledge"],
                    "status": status,
                    "note_quotes": list(dict.fromkeys(item["quotes"])),
                    "reason": "；".join(dict.fromkeys(item["reasons"])),
                    "rules": [routes[r] for r in rule_ids],
                    "note_path": f"references/sources/source-{n:03}/notes.md",
                    "mapping_review": "model_checked_not_independently_verified",
                }
            )
        # Persist progress before processing the next source.
        write_json(
            coverage_root / "knowledge-destinations.partial.json", {"items": rows, "missing_inventories": omissions}
        )
    report = {
        "schema_version": 1,
        "items": rows,
        "missing_inventories": omissions,
        "counts": {
            s: sum(r["status"] == s for r in rows) for s in ["incorporated", "retained_in_notes", "needs_review"]
        },
        "review_status": "automated_mapping_not_source_certification",
    }
    write_json(coverage_root / "knowledge-destinations.json", report)
    render_report(coverage_root / "knowledge-destinations.html", report)
    return report


def render_report(path, report):
    statuses = {"incorporated": "进入规则", "retained_in_notes": "保留在笔记", "needs_review": "需要复核"}
    rows = []
    for r in report["items"]:
        note_link = r["note_path"]
        readable = note_link.removesuffix("notes.md") + "document.html"
        if (path.parents[2] / readable).is_file():
            note_link = readable
        rule_links = (
            "、".join(
                f'<a href="../../{html.escape(x["condition_record"], quote=True)}">{html.escape(x["rule_id"])}</a>'
                for x in r["rules"]
            )
            or "无已关联规则"
        )
        capability_links = "、".join(
            f'<a href="../../{html.escape(capability, quote=True)}">能力指南</a>'
            for capability in dict.fromkeys(x["capability"] for x in r["rules"])
        )
        rows.append(
            "<tr><td>"
            + html.escape(r["knowledge"]["topic"])
            + "<br><small>"
            + html.escape(r["knowledge_id"])
            + "</small></td><td>"
            + statuses[r["status"]]
            + "</td><td>"
            + html.escape(r["knowledge"]["core"])
            + "<p>"
            + html.escape(r["reason"])
            + "</p></td><td>"
            + rule_links
            + ("<br>" + capability_links if capability_links else "")
            + '<br><a href="../../'
            + note_link
            + '">来源笔记</a></td></tr>'
        )
    warning = "；".join(x["source_id"] + "：" + x["reason"] for x in report["missing_inventories"])
    atomic_write(
        path,
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>知识点去向表</title><style>body{font:16px/1.6 system-ui;margin:32px;color:#243b32}table{border-collapse:collapse;width:100%}td,th{padding:14px;text-align:left;border-bottom:1px solid #dce4dc;overflow-wrap:anywhere}small{font-size:12px}a{color:#2a6347}input{padding:12px;font:inherit;width:100%;box-sizing:border-box}#table{overflow:auto}h1{font-size:28px}</style><h1>知识点去向表</h1><p>每个知识点记录进入规则、仅保留在笔记或需要复核。自动映射不能替代来源审阅。</p><p>'
        + html.escape(warning)
        + '</p><label>搜索知识、去向或规则<input type="search" id="q"></label><div id="table"><table><thead><tr><th>知识点</th><th>去向</th><th>内容与依据</th><th>规则与来源</th></tr></thead><tbody>'
        + "".join(rows)
        + '</tbody></table></div><script>document.getElementById("q").addEventListener("input",e=>{const q=e.target.value.toLowerCase();document.querySelectorAll("tbody tr").forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q))})</script></html>',
    )

"""把录制的审查回放整理成展示页用的 site/data.json（展示页 site/index.html 只读这个文件）。

  uv run python scripts/build_site.py [--runs runs/demo]

数据来源：runs/demo/*.json（scripts/record_demo.py 录制的真实运行）、evals/ 下的构造合同、
docs/numbers.md。只做整理和裁剪，不改任何意见、依据、耗时和花费。

合同示范文本（tpl-*）有版权、原文不进仓库：这里只输出名称和条目编号，不输出原文；
检索工具返回的原始文本也不输出，只保留命中了哪些片段。脚本最后会检查输出里没有示范文本的原文。
"""

# ruff: noqa: E501  （介绍和口径是整段中文，不按 100 字折行）
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
REPO = "https://github.com/kennywu411-dev/contract-review-agent"
NUMBERS_URL = f"{REPO}/blob/main/docs/numbers.md"

TENANTS = {
    "huaxin": "华信制造有限公司（构造）",
    "yunqi": "云启科技有限公司（构造）",
    "ruifeng": "瑞丰贸易有限公司（构造）",
}
GROUPS = {"pricing": "价款与履约", "liability": "责任与违约", "rights": "权利与争议"}
RISK_TYPES = {
    "penalty": "违约金过高或只约束一方",
    "liability": "免责或责任无上限",
    "termination": "单方解除权",
    "inspection": "检验期过短或视为合格",
    "payment": "付款周期过长或背靠背",
    "confidentiality": "保密缺失或不对等",
    "ip": "知识产权归属不利",
    "jurisdiction": "管辖不利或或裁或诉",
    "auto_renew": "自动续约",
    "deposit": "定金超过 20%",
    "unit_price": "单价高于台账历史价",
    "other": "其他",
}

# 展示哪几份、怎么介绍。介绍只描述合同是怎么构造的，审查结果全部来自运行记录。
REPLAYS = [
    {
        "id": "goods-r3",
        "tab": "先反问，再查台账比价",
        "tags": ["立场不明 → 反问", "MCP 查台账"],
        "note": "合同首部的甲方、乙方名称是空白的，看不出我方是哪一方。主审先反问，"
        "用户回答后才开始审查；价款组通过 DataPilot 的只读 MCP 查历史成交价，和合同单价比较。",
    },
    {
        "id": "v2-agri-r2",
        "tab": "长合同里的跨条款风险",
        "tags": ["跨条款组合", "依据不足 → 人工复核", "MCP 查台账"],
        "note": "27 条的长合同，来自难评测集 v2。第十条把预付款“同时作为定金”，第十一条又另收一笔定金，"
        "两条合起来才看得出问题。有一条意见的依据没能在检索到的原文里核实，校验后提示人工复核。",
    },
    {
        "id": "v2-dproc-r3",
        "tab": "我方是乙方",
        "tags": ["乙方立场", "要结合定义条款"],
        "note": "我方是受托方（乙方），同样来自难评测集 v2。第二十条的解除权单看是对等的，"
        "要结合第一条对“主要债务”的定义才看得出对我方不利。",
    },
    {
        "id": "agri-c2",
        "tab": "干净合同",
        "tags": ["没有植入风险", "含对我方有利的条款"],
        "note": "这份合同按构造没有植入风险，第九条是只约束对方、对我方有利的单方条款（没有被当成风险报出）。"
        "审查结果里没有高、中等级的意见，但报了 1 条低等级意见并提示人工复核，按标准答案属于多报。"
        "录制时一共试了 4 份干净合同，每份都至少报了 1 条意见，另外 3 份有中等级意见；"
        "干净合同上的误报率见下面的数字。",
    },
]

# 数字区：每个数字都要能在 docs/numbers.md 里找到（evidence 是必须原样出现在该文件里的片段）
NUMBERS = [
    {
        "value": "6.6% → 0.8%",
        "title": "无依据且没有提示复核的意见",
        "scope": "依据校验前后对比。42 份有意见的合同、122 条意见，由 Sonnet 5.5 逐条评判理由是否有原文依据；"
        "有依据却被新标为转人工的（误拦）占 2.0%。",
        "anchor": "依据校验cra-review-d4adr-008",
        "evidence": [
            "| 无依据且未标转人工（校验前 → 校验后） | 6.6% → 0.8% |",
            "122 条意见",
            "| 2.0% |",
        ],
    },
    {
        "value": "75 / 75",
        "title": "难评测集 v2 的风险检出",
        "scope": "60 份长合同里植入的 75 处风险，改进后的多 Agent 两次运行检出率都是 100%；"
        "改进前的多 Agent 是 89.3% 和 88.0%，单 Agent 是 96.0% 和 97.3%。合同是自己构造的。",
        "anchor": "难评测集-v2adr-013",
        "evidence": [
            "| 检出率（75 处） | 96.0% | 97.3% | 89.3% | 88.0% | 100% | 100% |",
        ],
    },
    {
        "value": "13–14 条 → 4–6 条",
        "title": "v1 评测集干净合同上的误报意见",
        "scope": "v1 评测集的 12 份干净合同上，高、中等级的误报意见数：单 Agent 两次运行是 13 和 14 条，"
        "多 Agent 两次是 6 和 4 条。这是 v1 评测集、多 Agent 改进前的数字。",
        "anchor": "多-agent-审查adr-012",
        "evidence": ["| 干净合同上的误报意见数（高/中） | 13 | 14 | 6 | 4 |"],
    },
    {
        "value": "53.3% → 76.7–83.3%",
        "title": "代价：改进后误报回升",
        "tone": "cost",
        "scope": "难评测集 v2 的 30 份干净合同里，被报出高、中等级意见的比例：多 Agent 改进前两次都是 53.3%，"
        "改进后是 76.7% 和 83.3%（单 Agent 是 90.0% 和 70.0%）。"
        "为了不漏报换来的，默认仍用改进后的版本。",
        "anchor": "难评测集-v2adr-013",
        "evidence": [
            "| 干净合同误报（高/中，30 份） | 90.0% | 70.0% | 53.3% | 53.3% | 76.7% | 83.3% |"
        ],
    },
    {
        "value": "$0.0038 – $0.0118",
        "title": "单份合同的审查花费（P50）",
        "scope": "模型 Haiku 5.5，不含依据校验。单 Agent $0.0038；多 Agent 在 v1 评测集上是 $0.0062 和 $0.0055，"
        "改进后在更长的 v2 合同上是 $0.0118 和 $0.0115。上面每份回放的花费是含校验的实际值。",
        "anchor": "难评测集-v2adr-013",
        "evidence": [
            "| 单份花费 P50 / 合计 | $0.0038 / $0.1726 | $0.0038 / $0.1781 | $0.0062 / $0.2738 "
            "| $0.0055 / $0.2688 |",
            "$0.0118 / $0.7066 | $0.0115 / $0.6957 |",
        ],
    },
    {
        "value": "27.6 – 43.4 秒",
        "title": "多 Agent 审查一份合同的耗时（P50）",
        "scope": "挂钟时间，不含依据校验。v1 评测集两次运行是 27.6 秒和 28.7 秒；"
        "改进后在 v2 长合同上是 43.4 秒和 41.1 秒。检索和重排模型跑在本机。",
        "anchor": "多-agent-审查adr-012",
        "evidence": [
            "| 挂钟耗时 P50 / P95 | 本机负载干扰 | 28.3s / 40.1s | 27.6s / 43.7s | 28.7s / 57.6s |",
            "I1、I2 的挂钟 P50 分别是 43.4s 和 41.1s",
        ],
    },
]

_HEAD = re.compile(r"^\s*(第[零一二三四五六七八九十百\d]+条)(?!的|约定|规定|所|中|之|项下|第)")


def split_contract(text: str) -> tuple[list[str], list[dict]]:
    """首部各行 + 条款列表（和 multi_agent.split_contract 同一条规则）。"""
    header: list[str] = []
    clauses: list[dict] = []
    for line in text.splitlines():
        m = _HEAD.match(line)
        if m:
            clauses.append({"no": m.group(1), "title": line.strip(), "body": []})
        elif clauses:
            clauses[-1]["body"].append(line)
        else:
            header.append(line)
    return header, clauses


def load_contracts() -> dict:
    out = {}
    for name in ("contracts.jsonl", "contracts_v2.jsonl"):
        for line in (ROOT / "evals" / name).read_text(encoding="utf-8").splitlines():
            if line.strip():
                c = json.loads(line)
                out[c["id"]] = c | {"file": f"evals/{name}"}
    return out


def doc_kind(doc_id: str) -> str:
    if doc_id.startswith("law-"):
        return "law"
    if doc_id.startswith("tpl-"):
        return "template"
    return "policy"


class Sources:
    """依据原文：按 chunk 去重。示范文本不带原文。"""

    def __init__(self) -> None:
        self.chunks: dict[str, dict] = {}
        self.withheld: list[str] = []  # 没有输出的示范文本原文，留给最后的检查用

    def add(self, c: dict) -> str:
        key = c["chunk_id"]
        if key not in self.chunks:
            kind = doc_kind(c["doc_id"])
            if kind == "template":
                self.withheld.append(c["text"])
            self.chunks[key] = {
                "kind": kind,
                "title": c["title"],
                "path": c.get("section_path", []),
                "articles": c.get("articles", []),
                "pages": c.get("pages", []),
                "text": None if kind == "template" else c["text"],
            }
        return key


def build_turn(turn: dict, sources: Sources) -> dict:
    trace, ver = turn["trace"], turn["verify"]
    result = trace["result"]
    ends = {(e["agent"], e["step"]): e["t_ms"] for e in turn["events"]}
    planner_step = next(s for s in trace["steps"] if s["agent"] == "planner")
    plan_call = planner_step["tool_calls"][0]
    out = {
        "question": turn["question"],
        "kind": result["kind"],
        "trace_id": trace["trace_id"],
        "model": trace["model"],
        "wall_ms": turn["wall_ms"],
        "review_ms": trace["latency_ms"],
        "review_cost": trace["cost_usd"],
        "verify_ms": ver["latency_ms"],
        "verify_cost": ver["cost_usd"],
        "model_calls": len(trace["steps"]) + (1 if ver["cost_usd"] else 0),
        "planner": {
            "tool": plan_call["name"],
            "llm_ms": planner_step["llm_ms"],
            "cost": planner_step["cost_usd"],
            "tokens_out": planner_step["usage"]["output_tokens"],
            "our_side": result.get("our_side", ""),
            "clarification": result.get("clarification", ""),
        },
    }
    if result["kind"] != "review":
        return out

    # 引用编号：全局编号 → chunk 或查表结果
    citations = {}
    for cid, c in trace["citations"].items():
        if c["kind"] == "table":
            citations[cid] = {"sql": c["sql"], "columns": c["columns"], "rows": c["rows"]}
        else:
            citations[cid] = {"chunk": sources.add(c)}
    out["citations"] = citations
    out["plan"] = {
        "assignments": trace["assignments"],
        "definitions": trace.get("definitions", []),
        "auto_assigned": trace.get("auto_assigned", {}),
    }

    # 各组的工具调用。组内编号（C1、T1…）按组的先后顺序重编成全局编号，和 multi_agent._renumber 一致
    offsets = {"C": 0, "T": 0}
    agents = []
    submitted: dict[tuple, list[str]] = {}
    for g, label in GROUPS.items():
        steps, local = [], {"C": 0, "T": 0}
        for s in (s for s in trace["steps"] if s["agent"] == g):
            calls = []
            for call in s["tool_calls"]:
                item = {"name": call["name"], "ms": call.get("ms")}
                if call["name"] == "submit_review":
                    item["n_findings"] = len(call["input"].get("findings", []))
                    for f in call["input"].get("findings", []):
                        submitted.setdefault((f.get("clause", ""), f.get("risk_type")), []).append(
                            g
                        )
                else:
                    item["input"] = call["input"].get("query") or call["input"].get("sql") or ""
                    item["is_error"] = bool(call.get("is_error"))
                    hits = []
                    for cid in call.get("citations", []):
                        kind, n = cid[0], int(cid[1:])
                        local[kind] = max(local[kind], n)
                        gid = f"{kind}{offsets[kind] + n}"
                        src = trace["citations"][gid]
                        if kind == "T":
                            assert src["sql"] == call["input"]["sql"], (gid, cid)
                        else:
                            assert f"[{cid}] 《{src['title']}》" in call["output"], (gid, cid)
                        hits.append(gid)
                    item["hits"] = hits
                calls.append(item)
            steps.append(
                {
                    "llm_ms": s["llm_ms"],
                    "cost": s["cost_usd"],
                    "tokens_out": s["usage"]["output_tokens"],
                    "end_ms": ends.get((g, s["step"])),
                    "calls": calls,
                }
            )
        for kind in offsets:
            offsets[kind] += local[kind]
        agents.append(
            {
                "group": g,
                "label": label,
                "clauses": trace["assignments"].get(g, []),
                "steps": steps,
            }
        )
    out["agents"] = agents
    out["summary"] = result["summary"]

    findings = []
    before = result["findings"]
    after = ver["findings"] or before
    for raw, f in zip(before, after, strict=True):
        findings.append(
            {
                "clause": f["clause"],
                "clause_raw": f["clause_raw"],
                "quote": f["quote"],
                "risk_type": f["risk_type"],
                "risk_label": RISK_TYPES.get(f["risk_type"], f["risk_type"]),
                "level": f["level"],
                "reason": f["reason"],
                "suggestion": f["suggestion"],
                "citations": f["citations"],
                "needs_human_review": f["needs_human_review"],
                "human_by_model": raw["needs_human_review"],
                "verify_flags": f.get("verify_flags", []),
                "revised_from": f.get("revised_from", ""),
                "cited_by_model": f.get("cited_by_model", []),
                "groups": submitted.get((f["clause_raw"], f["risk_type"]), []),
            }
        )
    out["findings"] = findings
    out["verify"] = {
        "ran": bool(ver["cost_usd"]),
        "flagged": ver["flagged"],
        "revised": ver["revised"],
        "repaired": ver["repaired"],
        "error": ver["verifier_error"],
    }
    return out


def check_numbers() -> None:
    text = (ROOT / "docs" / "numbers.md").read_text(encoding="utf-8")
    for n in NUMBERS:
        for e in n["evidence"]:
            assert e in text, f"docs/numbers.md 里找不到：{e}"


def check_withheld(payload: str, withheld: list[str], allowed: str, size: int = 14) -> list[str]:
    """输出里不应出现示范文本原文：任何长度 ≥ size 的连续片段都算（文件名称、法条、规范和合同里本来就有的除外）。"""
    leaks = []
    for text in withheld:
        text = re.sub(r"\s+", "", text)
        for i in range(0, max(len(text) - size, 0)):
            piece = text[i : i + size]
            if piece in payload and piece not in allowed:
                leaks.append(piece)
    return sorted(set(leaks))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--runs", type=Path, default=ROOT / "runs" / "demo")
    args = p.parse_args()

    check_numbers()
    contracts = load_contracts()
    sources = Sources()
    replays = []
    for spec in REPLAYS:
        rec = json.loads((args.runs / f"{spec['id']}.json").read_text(encoding="utf-8"))
        c = contracts[spec["id"]]
        header, clauses = split_contract(c["text"])
        turns = [build_turn(t, sources) for t in rec["turns"]]
        for t in turns:  # 摘录必须能在公开的合同文本里找到
            for f in t.get("findings", []):
                assert not f["quote"] or f["clause"] is None or f["quote"] in c["text"], f["quote"]
        replays.append(
            spec
            | {
                "title": c["title"],
                "tenant": TENANTS[c["tenant"]],
                "source": c["file"],
                "sql_backend": rec["sql_backend"],
                "header": header,
                "clauses": clauses,
                "turns": turns,
                "totals": {
                    "wall_ms": sum(t["wall_ms"] for t in turns),
                    "cost": sum(t["review_cost"] + t["verify_cost"] for t in turns),
                    "model_calls": sum(t["model_calls"] for t in turns),
                    "turns": len(turns),
                },
            }
        )
    data = {
        "repo": REPO,
        "numbers_url": NUMBERS_URL,
        "groups": GROUPS,
        "numbers": [{k: v for k, v in n.items() if k != "evidence"} for n in NUMBERS],
        "replays": replays,
        "chunks": sources.chunks,
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    allowed = re.sub(
        r"\s+",
        "",
        "".join(c["title"] + (c["text"] or "") for c in sources.chunks.values())
        + "".join(c["text"] for c in contracts.values()),
    )
    leaks = check_withheld(re.sub(r"\s+", "", payload), sources.withheld, allowed)
    assert not leaks, f"输出里出现了示范文本原文：{leaks[:5]}"
    assert "/Users/" not in payload and "sk-ant" not in payload
    SITE.mkdir(exist_ok=True)
    (SITE / "data.json").write_text(payload + "\n", encoding="utf-8")
    n_find = sum(len(t.get("findings", [])) for r in replays for t in r["turns"])
    print(
        f"site/data.json：{len(replays)} 份回放，{n_find} 条意见，"
        f"{len(sources.chunks)} 个依据片段（其中示范文本 {len(sources.withheld)} 个不带原文），"
        f"{len(payload) / 1024:.0f} KB"
    )


if __name__ == "__main__":
    main()

"""D4 评测用的独立评判：Sonnet 5.5（校验器用 Haiku，两者分开，避免自己判自己）。

一题只调一次：同时评原始回答和校验后回答（校验后的句子是原始句子的子集，直接复用逐句标注）。
"""

from __future__ import annotations

import json

from contract_review.llm import PRICING, SONNET, cost_usd
from contract_review.prompts import JUDGE_PROMPT
from contract_review.verify import _source_text

CORRECT = ["correct", "partially_correct", "incorrect", "not_applicable"]
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, "supported": {"type": "boolean"}},
                "required": ["index", "supported"],
                "additionalProperties": False,
            },
        },
        "revised": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, "supported": {"type": "boolean"}},
                "required": ["index", "supported"],
                "additionalProperties": False,
            },
        },
        "raw_correct": {"type": "string", "enum": CORRECT},
        "verified_correct": {"type": "string", "enum": CORRECT},
        "raw_refusal_ok": {"type": "boolean"},
        "verified_refusal_ok": {"type": "boolean"},
    },
    "required": [
        "sentences",
        "raw_correct",
        "verified_correct",
        "raw_refusal_ok",
        "verified_refusal_ok",
    ],
    "additionalProperties": False,
}
ANSWERED = ("answer", "partial")


class Judge:
    def __init__(self, llm) -> None:
        self.llm = llm

    def judge(self, q, raw, ver) -> dict:
        expect = (
            "该题标注为应拒答：已收录文件中没有答案。"
            if q.type == "refuse"
            else f"标准答案要点：{q.answer_hint}"
        )
        sources = (
            "\n".join(f"[{cid}] {_source_text(c)}" for cid, c in raw.citations.items())
            or "（没有检索到原文）"
        )
        if raw.kind in ("refuse", "clarify", "fallback"):
            raw_desc = f"（原始回答为{raw.kind}：{raw.refuse_reason or raw.clarification}）"
        else:
            raw_desc = "\n".join(
                f"{i}. [{s['kind']}] {s['text']}" for i, s in enumerate(raw.sentences)
            ) + (f"\n（声明答不了的部分：{raw.unanswered}）" if raw.unanswered else "")
        if ver.kind in ("refuse", "clarify"):
            ver_desc = f"校验后：{ver.kind}，{ver.refuse_reason or ver.clarification}"
        else:
            ver_desc = f"校验后保留的句子编号：{[s['index'] for s in ver.kept]}"
            revised = [s for s in ver.kept if s.get("revised_from")]
            if revised:
                ver_desc += (
                    "\n其中以下句子被校验器改写过（按改写后的文本判断 supported，填进 revised）：\n"
                )
                ver_desc += "\n".join(f"{s['index']}. {s['text']}" for s in revised)
        content = (
            f"{JUDGE_PROMPT}\n\n## 问题\n{q.question}\n\n## 评测标注\n{expect}\n\n"
            f"## 原文\n{sources}\n\n## 原始回答\n{raw_desc}\n\n## {ver_desc}"
        )
        resp = self.llm.create(
            max_tokens=4000,
            messages=[{"role": "user", "content": content}],
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": JUDGE_SCHEMA},
            },
        )
        text = next(b.text for b in resp.content if b.type == "text")
        out = json.loads(text)
        model = getattr(resp, "model", self.llm.model)
        out["cost_usd"] = cost_usd(model if model in PRICING else SONNET, resp.usage)
        out["judge_model"] = model
        return out


def score_d4(q, raw, ver, j: dict) -> dict:
    sup = {x["index"]: x["supported"] for x in j["sentences"]}
    rev_sup = {x["index"]: x["supported"] for x in j.get("revised", [])}
    revised = {s["index"] for s in ver.kept if s.get("revised_from")}

    def ver_ok(i: int) -> bool:
        return rev_sup.get(i, False) if i in revised else bool(sup.get(i))

    raw_facts = (
        [i for i, s in enumerate(raw.sentences) if s.get("kind", "fact") == "fact"]
        if raw.kind in ANSWERED
        else []
    )
    ver_facts = (
        [s["index"] for s in ver.kept if s.get("kind") == "fact"] if ver.kind in ANSWERED else []
    )
    return {
        "qid": q.id,
        "type": q.type,
        "subtype": q.subtype,
        "raw_kind": raw.kind,
        "ver_kind": ver.kind,
        "raw_fact_n": len(raw_facts),
        "raw_fact_supported": sum(1 for i in raw_facts if sup.get(i)),
        "ver_fact_n": len(ver_facts),
        "ver_fact_supported": sum(1 for i in ver_facts if ver_ok(i)),
        "raw_hallucinated": any(not sup.get(i) for i in raw_facts),
        "ver_hallucinated": any(not ver_ok(i) for i in ver_facts),
        "raw_correct": j["raw_correct"],
        "ver_correct": j["verified_correct"],
        "raw_refusal_ok": j["raw_refusal_ok"],
        "ver_refusal_ok": j["verified_refusal_ok"],
        "dropped": len(ver.dropped),
        "repaired": ver.repaired,
        "revised": ver.revised,
        "judge_cost_usd": j["cost_usd"],
        "judge_model": j["judge_model"],
    }


def result_from_trace(trace: dict):
    """从 trace 还原 agent 的原始回答，用来只换校验策略、重新评判（同一批回答上的对照实验）。"""
    from contract_review.agent import AgentResult

    res = trace["result"]
    return AgentResult(
        question=trace["question"],
        tenant=trace.get("tenant", trace.get("school", "")),
        kind=res["kind"],
        sentences=res.get("sentences", []),
        refuse_reason=res.get("refuse_reason", ""),
        unanswered=res.get("unanswered", ""),
        clarification=res.get("clarification", ""),
        invalid_citations=res.get("invalid_citations", []),
        citations=trace.get("citations", {}),
        steps=len(trace.get("steps", [])),
        cost_usd=trace.get("cost_usd", 0.0) - (trace.get("verify") or {}).get("cost_usd", 0.0),
        latency_ms=trace.get("latency_ms", 0.0)
        - (trace.get("verify") or {}).get("latency_ms", 0.0),
        trace={k: v for k, v in trace.items() if k not in ("verify", "judge")},
    )


def _rate(n: int, d: int) -> float:
    return n / d if d else 0.0


def summarize_d4(rows: list[dict]) -> dict:
    ans = [r for r in rows if r["type"] != "refuse" and r["subtype"] != "clarify"]
    ref = [r for r in rows if r["type"] == "refuse"]
    out: dict = {"n": len(rows)}
    for v in ("raw", "ver"):
        key = "raw" if v == "raw" else "verified"
        answered = [r for r in rows if r[f"{v}_kind"] in ANSWERED]
        out.setdefault("citation_accuracy", {})[key] = _rate(
            sum(r[f"{v}_fact_supported"] for r in rows), sum(r[f"{v}_fact_n"] for r in rows)
        )
        out.setdefault("hallucination_rate", {})[key] = _rate(
            sum(1 for r in answered if r[f"{v}_hallucinated"]), len(answered)
        )
        a = out.setdefault("answerable", {"n": len(ans)})
        a.setdefault("correct", {})[key] = _rate(
            sum(1 for r in ans if r[f"{v}_correct"] == "correct"), len(ans)
        )
        a.setdefault("correct_or_partial", {})[key] = _rate(
            sum(1 for r in ans if r[f"{v}_correct"] in ("correct", "partially_correct")), len(ans)
        )
        a.setdefault("false_refusal", {})[key] = _rate(
            sum(1 for r in ans if r[f"{v}_kind"] in ("refuse", "fallback")), len(ans)
        )
        f = out.setdefault("refuse", {"n": len(ref)})
        f.setdefault("strict_rate", {})[key] = _rate(
            sum(1 for r in ref if r[f"{v}_kind"] in ("refuse", "fallback")), len(ref)
        )
        f.setdefault("lenient_rate", {})[key] = _rate(
            sum(1 for r in ref if r[f"{v}_kind"] in ("refuse", "fallback") or r[f"{v}_refusal_ok"]),
            len(ref),
        )
    out["dropped_sentences"] = sum(r["dropped"] for r in rows)
    out["repaired_citations"] = sum(r["repaired"] for r in rows)
    out["revised_sentences"] = sum(r.get("revised", 0) for r in rows)
    out["judge_cost_usd"] = sum(r["judge_cost_usd"] for r in rows)
    return out


def run_d4_eval(questions, answer_one, judge: Judge, tag: str, max_cost_usd: float = 5.0):
    """answer_one(q) -> (AgentResult, VerifiedAnswer)。逐题落盘；问答 + 评判累计超预算就停。"""
    import statistics as st
    from datetime import datetime

    from contract_review.eval_retrieval import RUNS_DIR
    from contract_review.trace import save_trace

    out = RUNS_DIR / f"{tag}-{datetime.now():%Y%m%d-%H%M%S}"
    (out / "traces").mkdir(parents=True)
    rows, spent = [], 0.0
    with (out / "d4_results.jsonl").open("w", encoding="utf-8") as f:
        for q in questions:
            raw, ver = answer_one(q)
            j = judge.judge(q, raw, ver)
            raw.trace["judge"] = j
            save_trace(raw, out / "traces")
            row = score_d4(q, raw, ver, j) | {
                "trace_id": raw.trace["trace_id"],
                "answer_cost_usd": raw.cost_usd + ver.cost_usd,
                "verify_cost_usd": ver.cost_usd,
                "latency_ms": raw.latency_ms + ver.latency_ms,
                "verify_ms": ver.latency_ms,
            }
            rows.append(row)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            spent += row["answer_cost_usd"] + row["judge_cost_usd"]
            if spent > max_cost_usd:
                break
    s = summarize_d4(rows)
    lat = sorted(r["latency_ms"] for r in rows)
    cost = sorted(r["answer_cost_usd"] for r in rows)
    s |= {
        "run_id": out.name,
        "stopped_by_budget": len(rows) < len(questions),
        "latency_ms": {
            "p50": st.median(lat),
            "p95": lat[min(len(lat) - 1, int(0.95 * len(lat)))],
            "verify_mean": st.mean(r["verify_ms"] for r in rows),
        },
        "answer_cost_usd": {
            "total": sum(cost),
            "p50": st.median(cost),
            "p95": cost[min(len(cost) - 1, int(0.95 * len(cost)))],
        },
        "clarify_rate": _rate(
            sum(1 for r in rows if r["subtype"] == "clarify" and r["ver_kind"] == "clarify"),
            sum(1 for r in rows if r["subtype"] == "clarify"),
        ),
    }
    (out / "summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), "utf-8")
    return out

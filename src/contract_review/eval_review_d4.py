"""合同审查的 D4 评测：复用一次审查运行的原始意见，只加校验（Haiku）和评判（Sonnet）。

评判员逐条判断意见理由里的规定性说法能否由检索原文支撑（grounded），并给修改建议打 1–5 分。
口径（分母都是计分合同里的全部意见）：
- 原始幻觉率：理由无原文依据的意见占比。
- 未拦截的幻觉率：无依据、且没有被标"转人工复核"的意见占比，这才是会直接误导用户的部分。
  校验前看模型自己的标记，校验后看校验器加上的标记；被改写的意见按改写后的理由判断。
- 误拦率：有依据的意见被校验器新标成转人工的比例（分母是有依据的意见）。
"""

from __future__ import annotations

import json
import statistics as st
from datetime import datetime
from pathlib import Path

from contract_review.eval_retrieval import RUNS_DIR
from contract_review.llm import cost_usd
from contract_review.review_verify import VerifiedReview, unretrieved_refs, verify_review
from contract_review.verify import _source_text

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "grounded": {"type": "boolean"},
                    "unsupported_claims": {"type": "array", "items": {"type": "string"}},
                    "suggestion_score": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                },
                "required": ["index", "grounded", "unsupported_claims", "suggestion_score"],
                "additionalProperties": False,
            },
        },
        "revised": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, "grounded": {"type": "boolean"}},
                "required": ["index", "grounded"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["findings", "revised"],
    "additionalProperties": False,
}


class ReviewJudge:
    def __init__(self, llm) -> None:
        self.llm = llm

    def judge(self, contract: str, findings: list[dict], ver: VerifiedReview, citations: dict):
        from contract_review.prompts import REVIEW_JUDGE_PROMPT

        sources = "\n".join(f"[{cid}] {_source_text(c)}" for cid, c in citations.items())
        items = "\n".join(
            f'<意见 index="{i}">条款：{f.get("clause_raw") or f.get("clause")}\n'
            f"摘录：{f['quote']}\n风险类型：{f['risk_type']}；等级：{f['level']}\n"
            f"理由：{f['reason']}\n修改建议：{f['suggestion']}</意见>"
            for i, f in enumerate(findings)
        )
        revised = "\n".join(
            f'<改写 index="{i}">{g["reason"]}</改写>'
            for i, g in enumerate(ver.findings)
            if g.get("revised_from")
        )
        content = (
            f"{REVIEW_JUDGE_PROMPT}\n## 合同\n<合同>\n{contract}\n</合同>\n\n"
            f"## 检索到的原文\n{sources or '（没有检索到原文）'}\n\n## 审查意见\n{items}\n\n"
            f"## 校验器改写过的理由（没有则为空）\n{revised}"
        )
        resp = self.llm.create(
            max_tokens=6000,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
        )
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text), cost_usd(self.llm.model, resp.usage)


def score_review_d4(
    cid: str, raw: list[dict], ver: VerifiedReview, judgment: dict, unretrieved_raw: int
) -> dict:
    by = {x["index"]: x for x in judgment["findings"]}
    rev = {x["index"]: x["grounded"] for x in judgment.get("revised", [])}
    n = len(raw)
    grounded = [by.get(i, {}).get("grounded", False) for i in range(n)]
    after = [
        rev.get(i, grounded[i]) if ver.findings[i].get("revised_from") else grounded[i]
        for i in range(n)
    ]
    return {
        "id": cid,
        "n": n,
        "raw_ungrounded": sum(not g for g in grounded),
        "raw_ungrounded_unflagged": sum(
            not g and not f["needs_human_review"] for g, f in zip(grounded, raw, strict=True)
        ),
        "after_ungrounded_unflagged": sum(
            not g and not f["needs_human_review"] for g, f in zip(after, ver.findings, strict=True)
        ),
        "grounded_raw": sum(grounded),
        "false_flags": sum(
            g and bool(v.get("verify_flags")) and not r["needs_human_review"]
            for g, v, r in zip(grounded, ver.findings, raw, strict=True)
        ),
        "flagged": ver.flagged,
        "revised": ver.revised,
        "repaired": ver.repaired,
        "unretrieved_raw": unretrieved_raw,
        "suggestion_scores": [by.get(i, {}).get("suggestion_score", 0) for i in range(n)],
        "unsupported_claims": [by.get(i, {}).get("unsupported_claims", []) for i in range(n)],
    }


def _r(a: float, b: float) -> float:
    return a / b if b else 0.0


def summarize_review_d4(rows: list[dict]) -> dict:
    n = sum(r["n"] for r in rows)
    scores = [x for r in rows for x in r["suggestion_scores"] if x]
    return {
        "contracts": len(rows),
        "findings": n,
        "hallucination": {
            "raw_rate": _r(sum(r["raw_ungrounded"] for r in rows), n),
            "raw_unflagged_rate": _r(sum(r["raw_ungrounded_unflagged"] for r in rows), n),
            "after_unflagged_rate": _r(sum(r["after_ungrounded_unflagged"] for r in rows), n),
            "false_flag_rate": _r(
                sum(r["false_flags"] for r in rows), sum(r["grounded_raw"] for r in rows)
            ),
        },
        "unretrieved_ref_rate": _r(sum(r["unretrieved_raw"] for r in rows), n),
        "flagged": sum(r["flagged"] for r in rows),
        "revised": sum(r["revised"] for r in rows),
        "repaired": sum(r["repaired"] for r in rows),
        "suggestion": {
            "n": len(scores),
            "mean": st.mean(scores) if scores else 0.0,
            "ge4_rate": _r(sum(1 for x in scores if x >= 4), len(scores)),
        },
        "cost": {
            "verify_usd": sum(r.get("verify_cost_usd", 0.0) for r in rows),
            "judge_usd": sum(r.get("judge_cost_usd", 0.0) for r in rows),
        },
    }


def run_review_d4(source: Path, contracts: dict, verifier, judge, tag: str, max_cost: float):
    """source：一次 eval-review 运行目录。预算只算本次新产生的校验和评判花费（P18）。"""
    out = RUNS_DIR / f"{tag}-{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True)
    rows, spent = [], 0.0
    results = [json.loads(x) for x in (source / "review_results.jsonl").open(encoding="utf-8")]
    with (out / "d4_results.jsonl").open("w", encoding="utf-8") as fh:
        for res in results:
            c = contracts[res["id"]]
            trace = json.loads((source / "traces" / f"{res['trace_id']}.json").read_text("utf-8"))
            r = trace["result"] | {"citations": trace["citations"]}
            if c.clarify or r["kind"] != "review" or not r["findings"]:
                continue
            ver = verify_review(r, verifier, c.tenant, c.text)
            judgment, jcost = judge.judge(c.text, r["findings"], ver, r["citations"])
            unretrieved = sum(
                bool(unretrieved_refs(f, r["citations"], c.tenant)) for f in r["findings"]
            )
            row = score_review_d4(c.id, r["findings"], ver, judgment, unretrieved) | {
                "trace_id": res["trace_id"],
                "verify_cost_usd": ver.cost_usd,
                "judge_cost_usd": jcost,
                "verify_ms": ver.latency_ms,
                "verified_findings": ver.findings,
            }
            rows.append(row)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            spent += ver.cost_usd + jcost
            if spent > max_cost:
                break
    summary = summarize_review_d4(rows) | {
        "run_id": out.name,
        "kind": "review_d4",
        "source_run": source.name,
        "stopped_by_budget": spent > max_cost,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), "utf-8")
    return out

"""多轮对话评测（ADR 005）：同一组对话分别用 none / full / compress 三种记忆方式跑，评追问轮。

追问轮"正确"：answerable/table 轮要求答案要点对（Sonnet 评 verified_correct == correct）
且引用命中标准出处；refuse 轮要求拒答或明确声明边界（verified_refusal_ok）。首轮不评。
"""

from __future__ import annotations

import json
import statistics as st
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from contract_review.eval_retrieval import RUNS_DIR, is_hit
from contract_review.evalset import EVAL_DIR, Question
from contract_review.judge import Judge
from contract_review.memory import ConversationMemory
from contract_review.pipeline import answer_question, turn_from

DIALOGS_PATH = EVAL_DIR / "dialogs.jsonl"
COMPRESS_BUDGET = 300  # 故意设低：多数 3 轮以上的对话都会触发压缩，才测得出压缩的影响


@dataclass
class Dialog:
    id: str
    tenant: str
    pattern: str
    turns: list[dict]


def load_dialogs(path: Path = DIALOGS_PATH) -> list[Dialog]:
    return [
        Dialog(**json.loads(x)) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()
    ]


def _cite_hit(raw, ver, gold: list[dict], type_: str) -> bool:
    for s in ver.kept:
        for cid in s.get("citations", []):
            c = raw.citations.get(cid)
            if not c:
                continue
            if c["kind"] == "table" and type_ == "table" and c.get("rows"):
                return True
            if c["kind"] == "doc" and is_hit(
                SimpleNamespace(
                    doc_id=c["doc_id"], pages=c["pages"], articles=c["articles"], text=c["text"]
                ),
                gold,
            ):
                return True
    return False


def run_dialog(
    d: Dialog, mode: str, make_toolbox, llm, verifier, judge_llm, summarizer
) -> list[dict]:
    mem = ConversationMemory(
        mode=mode,
        budget_tokens=COMPRESS_BUDGET if mode == "compress" else 10**9,
        keep_last=1,
        summarizer=summarizer,
    )
    judge, rows, asked = Judge(judge_llm), [], []
    for i, t in enumerate(d.turns):
        history = mem.render()
        raw, ver = answer_question(
            t["question"], d.tenant, llm, make_toolbox(d.tenant), verifier, history=history
        )
        mem.add(turn_from(raw, ver))
        row = {
            "dialog": d.id,
            "pattern": d.pattern,
            "mode": mode,
            "turn": i,
            "type": t["type"],
            "needs_context": t.get("needs_context", False),
            "kind": ver.kind,
            "history_tokens": len(history),
            "compressions": mem.compressions,
            "answer_cost_usd": raw.cost_usd + ver.cost_usd,
            "input_tokens": sum(
                sum(
                    st_["usage"].get(k, 0)
                    for k in (
                        "input_tokens",
                        "cache_read_input_tokens",
                        "cache_creation_input_tokens",
                    )
                )
                for st_ in raw.trace["steps"]
            ),
            "latency_ms": raw.latency_ms + ver.latency_ms,
            "trace_id": raw.trace["trace_id"],
            "judged": False,
            "correct": None,
            "judge_cost_usd": 0.0,
        }
        if i > 0:
            context = "；".join(asked)
            q = Question(
                f"{d.id}-{i}",
                d.tenant,
                f"（对话上文，学生依次问过：{context}）当前追问：{t['question']}",
                t["type"],
                None,
                "doc",
                None,
                t["gold"],
                t["answer_hint"],
                "",
            )
            j = judge.judge(q, raw, ver)
            ok = (
                j["verified_refusal_ok"] or ver.kind == "refuse"
                if t["type"] == "refuse"
                else j["verified_correct"] == "correct"
                and _cite_hit(raw, ver, t["gold"], t["type"])
            )
            row |= {
                "judged": True,
                "correct": bool(ok),
                "verified_correct": j["verified_correct"],
                "judge_cost_usd": j["cost_usd"],
            }
            raw.trace["judge"] = j
        raw.trace["verify"] = asdict(ver)
        row["_trace"] = raw.trace
        rows.append(row)
        asked.append(t["question"])
    row_cost = mem.cost_usd
    if rows:
        rows[-1]["compress_cost_usd"] = row_cost
    return rows


def summarize_dialogs(rows: list[dict]) -> dict:
    out = {}
    for mode in sorted({r["mode"] for r in rows}):
        rs = [r for r in rows if r["mode"] == mode]
        judged = [r for r in rs if r["judged"]]
        by_pattern = {}
        for p in sorted({r["pattern"] for r in judged}):
            pr = [r for r in judged if r["pattern"] == p]
            by_pattern[p] = {"n": len(pr), "correct": sum(r["correct"] for r in pr) / len(pr)}
        hist = [r["history_tokens"] for r in rs if r["turn"] > 0]
        out[mode] = {
            "followup_n": len(judged),
            "followup_correct": sum(r["correct"] for r in judged) / len(judged) if judged else 0.0,
            "by_pattern": by_pattern,
            "history_tokens_mean": st.mean(hist) if hist else 0.0,
            "input_tokens_mean": st.mean(r["input_tokens"] for r in rs) if rs else 0.0,
            "cost_usd": sum(r["answer_cost_usd"] + r.get("compress_cost_usd", 0.0) for r in rs),
            "compressions": sum(
                r["compressions"]
                for r in rs
                if r["turn"] == max(x["turn"] for x in rs if x["dialog"] == r["dialog"])
            ),
            "latency_p50_ms": st.median(r["latency_ms"] for r in rs) if rs else 0.0,
        }
    out["judge_cost_usd"] = sum(r["judge_cost_usd"] for r in rows)
    return out


def run_dialog_eval(
    dialogs, modes, make_toolbox, llm, verifier, judge_llm, summarizer, tag, max_cost_usd=5.0
):
    out = RUNS_DIR / f"{tag}-{datetime.now():%Y%m%d-%H%M%S}"
    (out / "traces").mkdir(parents=True)
    rows, spent = [], 0.0
    with (out / "dialog_results.jsonl").open("w", encoding="utf-8") as f:
        for d in dialogs:
            for mode in modes:
                for r in run_dialog(d, mode, make_toolbox, llm, verifier, judge_llm, summarizer):
                    trace = r.pop("_trace")
                    (out / "traces" / f"{trace['trace_id']}.json").write_text(
                        json.dumps(trace, ensure_ascii=False, default=str), "utf-8"
                    )
                    rows.append(r)
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                    spent += (
                        r["answer_cost_usd"] + r["judge_cost_usd"] + r.get("compress_cost_usd", 0.0)
                    )
            f.flush()
            if spent > max_cost_usd:
                break
    s = summarize_dialogs(rows) | {
        "run_id": out.name,
        "dialogs": len({r["dialog"] for r in rows}),
        "stopped_by_budget": len({r["dialog"] for r in rows}) < len(dialogs),
        "spent_usd": spent,
    }
    (out / "summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), "utf-8")
    return out

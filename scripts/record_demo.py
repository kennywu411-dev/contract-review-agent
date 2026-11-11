"""录制展示页（site/）用的审查回放。

走和 Web 审查页相同的路径：`RealEngine.review`，即多 Agent 审查、金额测算加依据校验；
当事人空白的合同先得到反问，再把用户的回答交给主审。
每份合同的完整 trace 存到 runs/demo/<合同 id>.json，
`scripts/build_site.py` 读它生成 site/data.json。

  uv run python scripts/record_demo.py                 # 录默认的几份
  uv run python scripts/record_demo.py goods-c2        # 只录指定的合同

会调用 API，每份约 $0.01–0.03；超过 --max-cost 就停。需要先 `cra ingest`。
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from types import SimpleNamespace

from contract_review.config import ROOT
from contract_review.contract_evalset import load_contracts

OUT = ROOT / "runs" / "demo"
# 默认录制的合同：两份带金额测算的采购合同（v3）、反问 + 查台账比价、跨条款组合风险的长合同、
# 我方是乙方的长合同、干净合同
DEFAULT = ["v3-agri-07", "v3-agri-10", "goods-r3", "v2-agri-r2", "v2-dproc-r3", "agri-c2"]


def contracts() -> dict:
    cs = load_contracts() + load_contracts(ROOT / "evals" / "contracts_v2.jsonl")
    # v3（采购金额测算集）的字段和 v1、v2 不同，这里只用到 id、tenant、text、our_side
    for line in (ROOT / "evals" / "contracts_v3.jsonl").read_text("utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            cs.append(SimpleNamespace(**{k: d[k] for k in ("id", "tenant", "text", "our_side")}))
    return {c.id: c for c in cs}


def record(engine, c) -> dict:
    turns = []
    question = ""
    for _ in range(2):  # 最多两轮：反问后带上用户的回答再审一次
        events: list[dict] = []
        t0 = time.perf_counter()

        def on_step(rec, events=events, t0=t0):
            events.append(
                {
                    "agent": rec.get("agent", "planner"),
                    "step": rec.get("step"),
                    "t_ms": (time.perf_counter() - t0) * 1000,
                }
            )

        r, ver = engine.review(c.text, c.tenant, on_step, "", question, first_review=True)
        turns.append(
            {
                "question": question,
                "trace": r.trace,
                "verify": asdict(ver),
                "events": events,
                "wall_ms": (time.perf_counter() - t0) * 1000,
            }
        )
        if r.kind != "clarify":
            break
        question = f"我方是{c.our_side}方。"  # 用户的回答：按合同构造时设定的立场
    return {
        "contract_id": c.id,
        "tenant": c.tenant,
        "sql_backend": getattr(engine.sql, "name", None),
        "turns": turns,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("ids", nargs="*", default=DEFAULT)
    p.add_argument("--max-cost", type=float, default=0.5)
    args = p.parse_args()

    from contract_review.api import RealEngine

    by_id = contracts()
    engine = RealEngine()
    OUT.mkdir(parents=True, exist_ok=True)
    spent = 0.0
    try:
        for cid in args.ids:
            if spent >= args.max_cost:
                print(f"已花 ${spent:.4f}，达到上限，停止")
                break
            rec = record(engine, by_id[cid])
            cost = sum(t["trace"]["cost_usd"] + t["verify"]["cost_usd"] for t in rec["turns"])
            spent += cost
            (OUT / f"{cid}.json").write_text(
                json.dumps(rec, ensure_ascii=False, indent=1, default=str), "utf-8"
            )
            n = len(rec["turns"][-1]["trace"]["result"]["findings"])
            print(
                f"{cid}: {len(rec['turns'])} 轮，意见 {n} 条，${cost:.4f}，"
                f"查表后端 {rec['sql_backend']}"
            )
    finally:
        engine.close()
    print(f"合计 ${spent:.4f}")


if __name__ == "__main__":
    main()

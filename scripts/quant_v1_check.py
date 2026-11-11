"""在 v1 的合同上只跑金额测算（不跑审查），作为 v3 之外的旁证（ADR 014）。

v1 不是为金额设计的：7 类合同、我方甲乙各半。能和 v1 标准答案对上的只有两类，单价风险
（标准答案里有合同单价）和定金 30% / 40%；其余金额没有标准答案，只统计数量。

  uv run python scripts/quant_v1_check.py        # 会调用 API，约 $0.02
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from contract_review.config import ROOT
from contract_review.contract_evalset import load_contracts
from contract_review.llm import HAIKU, AnthropicLLM
from contract_review.metriclens_client import make_backend
from contract_review.quant_extract import run_quant


def main() -> None:
    cs = [c for c in load_contracts() if not c.clarify]
    llm, sql = AnthropicLLM(HAIKU), make_backend()
    lock = threading.Lock()

    class Locked:
        name = getattr(sql, "name", None)

        def run(self, q):
            with lock:
                return sql.run(q)

    def job(c):
        q = run_quant(c.text, c.tenant, c.our_side, llm, Locked())
        q.pop("step", None)
        return {"id": c.id, "our_side": c.our_side, "gold": c.gold, "quant": q}

    with ThreadPoolExecutor(4) as pool:
        rows = list(pool.map(job, cs))
    if hasattr(sql, "close"):
        sql.close()

    checks = []
    for r in rows:
        items = {i["key"]: i for i in r["quant"]["items"]}
        figs = {f["field"]: f["value"] for f in r["quant"]["figures"] if f["ok"]}
        for g in r["gold"]:
            exp = got = None
            if g["type"] == "unit_price":
                ln = r["quant"]["lines"][0] if r["quant"]["lines"] else {}
                if ln.get("ledger") and ln.get("unit_price") == g["price"]:
                    exp = round((g["price"] - ln["ledger"]["avg"]) * ln["quantity"], 2)
                got = items.get("price_overpay", {}).get("amount")
            elif g["type"] == "deposit":
                pct = 30 if g["variant"] == "R1" else 40
                if figs.get("contract_total"):
                    exp = round(figs["contract_total"] * (pct - 20) / 100, 2)
                got = items.get("deposit_excess", {}).get("amount")
            else:
                continue
            ok = exp is not None and got is not None and abs(exp - got) < 0.011
            checks.append({"id": r["id"], "type": g["type"], "expected": exp, "got": got, "ok": ok})

    out = ROOT / "runs" / f"quant-v1-{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True)
    (out / "results.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows), "utf-8"
    )
    figures = [f for r in rows for f in r["quant"]["figures"]]
    summary = {
        "kind": "quant_v1",
        "run": out.name,
        "contracts": len(rows),
        "by_side": dict(Counter(r["our_side"] for r in rows)),
        "cost_usd": sum(r["quant"]["cost_usd"] for r in rows),
        "errors": sum(r["quant"]["status"] != "ok" for r in rows),
        "checks": len(checks),
        "checks_ok": sum(c["ok"] for c in checks),
        "figures_total": len(figures),
        "figures_rejected": sum(1 for f in figures if not f["ok"] and not f.get("soft")),
        "items_by_status": dict(
            Counter(f"{i['key']}:{i['status']}" for r in rows for i in r["quant"]["items"])
        ),
        "check_rows": checks,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "check_rows"}, ensure_ascii=False))


if __name__ == "__main__":
    main()

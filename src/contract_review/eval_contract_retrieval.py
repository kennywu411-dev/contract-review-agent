"""合同场景的检索评测：拿风险条款的正文去检索，看前 k 条里有没有该风险的合法依据条文。

查询 = 金标准风险所在条款的正文（缺失型用"保密条款 保密义务"），模拟"拿着条款找依据"。
命中 = 前 k 个 chunk 里有一个的 (doc_id, 条号) 落在该风险的依据集合里。单价风险只有台账和规范依据，
台账走查表不走检索，所以只保留有法条或规范依据的条目。
"""

from __future__ import annotations

import json
import time
from datetime import datetime

from contract_review.contract_evalset import Contract
from contract_review.eval_agent import _pct
from contract_review.eval_retrieval import RUNS_DIR

MISSING_QUERY = {"confidentiality": "保密条款 保密义务 商业秘密"}


def _clause_body(text: str, clause: str) -> str:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"{clause} "):
            return lines[i + 1] if i + 1 < len(lines) else ""
    return ""


def build_queries(contracts: list[Contract]) -> list[dict]:
    out = []
    for c in contracts:
        for g in c.gold + c.borderline:
            if not g["basis"]:
                continue
            query = _clause_body(c.text, g["clause"]) if g["clause"] else MISSING_QUERY[g["type"]]
            key = (c.id, g["clause"], g["type"])
            if any((q["contract_id"], q["clause"], q["type"]) == key for q in out):
                continue
            out.append(
                {
                    "contract_id": c.id,
                    "tenant": c.tenant,
                    "clause": g["clause"],
                    "type": g["type"],
                    "query": query,
                    "basis": g["basis"],
                }
            )
    return out


def basis_hit(chunk, basis: list[list[str]]) -> bool:
    allowed = {(d, a) for d, a in basis}
    return any((chunk.doc_id, a) in allowed for a in chunk.articles)


def run_contract_retrieval_eval(retriever, queries: list[dict], modes, tag: str, k: int = 5):
    out = RUNS_DIR / f"{tag}-{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True)
    summary: dict = {"kind": "contract_retrieval", "n": len(queries), "k": k, "modes": {}}
    rows = []
    for mode in modes:
        hits, lat, stage = 0, [], {}
        for q in queries:
            t = time.perf_counter()
            res = retriever.search(q["query"], q["tenant"], k=k, mode=mode)
            lat.append((time.perf_counter() - t) * 1000)
            for name, ms in res.latency_ms.items():
                stage.setdefault(name, []).append(ms)
            hit = any(basis_hit(h.chunk, q["basis"]) for h in res.hits)
            hits += hit
            rows.append(
                {"mode": mode, "contract_id": q["contract_id"], "type": q["type"], "hit": hit}
            )
        summary["modes"][mode] = {
            "recall_at_k": hits / len(queries) if queries else 0.0,
            "latency_ms": {"p50": _pct(lat, 0.5), "p95": _pct(lat, 0.95)},
            "stage_p50_ms": {n: _pct(v, 0.5) for n, v in stage.items()},
        }
    summary["run_id"] = out.name
    (out / "results.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8"
    )
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), "utf-8")
    return out

"""合同审查评测：全部自动判分（修改建议的质量另由 Sonnet 抽样评，见 ADR 007）。

口径：
- 检出：金标准风险所在条款上有一条审查意见（缺失型：有一条 clause 为空、类型为保密的意见）。
- 类型判对：检出的意见里有一条的 risk_type 与金标准一致。
- 依据正确：检出的意见引用的原文里，至少一条落在该风险的合法依据集合（doc_id + 条号）；
  单价风险引用了有数据的查表结果也算。
- 误报：既不对应金标准风险、也不是临界条款的意见，按条款去重计数；
  落在"对我方有利"条款上的单独记为视角错误。
- 反问：4 份当事人空白的合同应当反问；其余合同反问算多余。反问合同不计入检出。

运行、续跑、预算闸门、配对比较、门禁和看板都交给评测平台 evalkit：
score_contract 是领域打分规则（evalkit 的打分器），
METRICS 用 evalkit.report.Ratio 声明跨合同累加的指标。
运行目录是 evalkit 格式（results.jsonl / config.json / traces/），另外派生一份 review_results.jsonl
（每份合同的逐项计数），依据校验、多轮追问、分析脚本和 Web 运行记录页都读它，格式不变。
"""

from __future__ import annotations

import hashlib
import json
import statistics as st
from datetime import datetime
from pathlib import Path

from evalkit import store
from evalkit.dataset import Dataset, Item
from evalkit.report import Ratio
from evalkit.runner import TaskResult, Variant
from evalkit.runner import run as evalkit_run
from evalkit.scorers import Score
from evalkit.stats import percentile

from contract_review.contract_evalset import RISK_TYPES, Contract
from contract_review.eval_retrieval import RUNS_DIR


def _basis_ok(f: dict, gold: dict, citations: dict) -> bool:
    allowed = {(d, a) for d, a in gold["basis"]}
    for cid in f["citations"]:
        c = citations.get(cid)
        if not c:
            continue
        if c["kind"] == "table":
            if gold["type"] == "unit_price" and c.get("rows"):
                return True
        elif any((c["doc_id"], a) in allowed for a in c.get("articles", [])):
            return True
    return False


def _matches(f: dict, gold: dict) -> bool:
    if gold["clause"] is None:
        return f["clause"] is None and f["risk_type"] == gold["type"]
    # v2 组合风险：落在任何一个相关条款上都算检出
    return f["clause"] in (gold.get("clauses") or [gold["clause"]])


def _type_ok(f: dict, gold: dict) -> bool:
    return f["risk_type"] == gold["type"] or f["risk_type"] in gold.get("alt_types", [])


def score_contract(c: Contract, r: dict) -> dict:
    findings = r["findings"] if r["kind"] == "review" else []
    used: set[int] = set()
    gold_rows = []
    for g in c.gold:
        hits = [i for i, f in enumerate(findings) if _matches(f, g)]
        used.update(hits)
        fs = [findings[i] for i in hits]
        gold_rows.append(
            {
                "clause": g["clause"],
                "type": g["type"],
                "level": g.get("level"),
                "category": g.get("category"),
                "detected": bool(fs),
                "type_ok": any(_type_ok(f, g) for f in fs),
                "basis_ok": any(_basis_ok(f, g, r["citations"]) for f in fs),
                "level_ok": any(f["level"] == g.get("level") for f in fs),
                "human": any(f["needs_human_review"] for f in fs),
            }
        )
    # 严格命中：条款和类型都对，每处风险最多算一条意见
    strict_hits = sum(1 for g in c.gold if any(_matches(f, g) and _type_ok(f, g) for f in findings))
    border = []
    for b in c.borderline:
        hits = [i for i, f in enumerate(findings) if f["clause"] == b["clause"]]
        used.update(hits)
        border.append(
            {
                "clause": b["clause"],
                "type": b["type"],
                "flagged": bool(hits),
                "human": any(findings[i]["needs_human_review"] for i in hits),
            }
        )
    acceptable = 0
    for i, f in enumerate(findings):
        if i not in used and any(
            f["clause"] in a["clauses"] and f["risk_type"] in a["types"] for a in c.acceptable
        ):
            used.add(i)
            acceptable += 1
    favorable = {f["clause"] for f in c.favorable}
    rest = [f for i, f in enumerate(findings) if i not in used]
    fav_flagged = {f["clause"] for f in rest if f["clause"] in favorable}
    fp_clauses = {f["clause"] or f"缺失:{f['risk_type']}" for f in rest} - fav_flagged
    # 高/中口径：低等级意见在产品里作为"提示"折叠显示，单独统计
    fp_hm = {
        f["clause"] or f"缺失:{f['risk_type']}" for f in rest if f["level"] != "低"
    } - fav_flagged
    matched_hm = sum(1 for i in used if findings[i]["level"] != "低")
    distractors = [
        {
            "kind": d["kind"],
            "name": d.get("name"),
            "flagged_hm": any(f["clause"] == d["clause"] and f["level"] != "低" for f in rest),
        }
        for d in c.distractors
    ]
    return {
        "id": c.id,
        "tenant": c.tenant,
        "contract_type": c.contract_type,
        "our_side": c.our_side,
        "clarify": c.clarify,
        "clean": not c.gold,
        "kind": r["kind"],
        "side_ok": r.get("our_side") == c.our_side if r["kind"] == "review" else None,
        "clarify_ok": r["kind"] == "clarify" if c.clarify else False,
        "gold": gold_rows,
        "borderline_n": len(border),
        "borderline_flagged": sum(b["flagged"] for b in border),
        "borderline_human": sum(b["human"] for b in border),
        "acceptable_flagged": acceptable,
        "favorable_n": len(favorable),
        "favorable_flagged": len(fav_flagged),
        "distractors": distractors,
        "fp": len(fp_clauses),
        "fp_hm": len(fp_hm),
        "n_findings_hm": sum(1 for f in findings if f["level"] != "低"),
        "matched_findings_hm": matched_hm,
        "n_findings": len(findings),
        "matched_findings": len(used),
        "strict_hits": strict_hits,
        "human_findings": sum(f["needs_human_review"] for f in findings),
        "quote_found": sum(f.get("quote_found", True) for f in findings),
        "invalid_citations": r.get("invalid_citations", []),
        "steps": r["steps"],
        "cost_usd": r["cost_usd"],
        "latency_ms": r["latency_ms"],
        "end": r["trace"].get("end"),
    }


# ---------- 指标：跨合同累加的比例（evalkit Ratio） ----------


def _scored(r: dict) -> bool:
    return not r["clarify"]


def _golds(r: dict) -> list[dict]:
    return r["gold"] if _scored(r) else []


def _det(r: dict) -> list[dict]:
    return [g for g in _golds(r) if g["detected"]]


def _clean(r: dict) -> bool:
    return _scored(r) and r["clean"]


def _risky(r: dict) -> bool:
    return _scored(r) and not r["clean"]


def _reviewed(r: dict) -> bool:
    return _scored(r) and r["kind"] == "review"


def _sum(key: str):
    return lambda r: r[key] if _scored(r) else 0


def _gold_rate(name: str, pred, over=_golds) -> Ratio:
    return Ratio(name, lambda r: sum(1 for g in over(r) if pred(g)), lambda r: len(over(r)))


METRICS = {
    m.name: m
    for m in [
        _gold_rate("recall", lambda g: g["detected"]),
        _gold_rate("type_accuracy", lambda g: g["type_ok"], over=_det),
        _gold_rate("basis_accuracy", lambda g: g["basis_ok"], over=_det),
        _gold_rate("level_agreement", lambda g: g["level_ok"], over=_det),
        _gold_rate("human_rate_on_gold", lambda g: g["human"], over=_det),
        Ratio("precision", _sum("matched_findings"), _sum("n_findings")),
        # 严格口径：只认条款+类型都对的意见，同一处风险的重复意见、临界和合理意见都不算命中
        Ratio("precision_strict", _sum("strict_hits"), _sum("n_findings")),
        Ratio("precision_hm", _sum("matched_findings_hm"), _sum("n_findings_hm")),
        Ratio("fp_per_risky_contract", lambda r: r["fp"] if _risky(r) else 0, _risky),
        # 干净合同上报出任何意见都算误报（含对我方有利条款上的视角错误）
        Ratio(
            "fp_contract_rate", lambda r: _clean(r) and r["fp"] + r["favorable_flagged"] > 0, _clean
        ),
        Ratio(
            "fp_per_contract",
            lambda r: r["fp"] + r["favorable_flagged"] if _clean(r) else 0,
            _clean,
        ),
        Ratio(
            "fp_contract_rate_hm",
            lambda r: _clean(r) and r["fp_hm"] + r["favorable_flagged"] > 0,
            _clean,
        ),
        Ratio("favorable_flag_rate", _sum("favorable_flagged"), _sum("favorable_n")),
        Ratio("side_accuracy", lambda r: _reviewed(r) and bool(r["side_ok"]), _reviewed),
        Ratio("borderline_flag_rate", _sum("borderline_flagged"), _sum("borderline_n")),
        Ratio("borderline_human_rate", _sum("borderline_human"), _sum("borderline_n")),
        Ratio("human_rate_all_findings", _sum("human_findings"), _sum("n_findings")),
        Ratio("clarify_rate", lambda r: r["clarify"] and r["clarify_ok"], lambda r: r["clarify"]),
        Ratio("unnecessary_clarify_rate", lambda r: _scored(r) and r["kind"] == "clarify", _scored),
        Ratio("quote_found_rate", _sum("quote_found"), _sum("n_findings")),
        Ratio(
            "invalid_citation_contract_rate",
            lambda r: _scored(r) and bool(r["invalid_citations"]),
            _scored,
        ),
    ]
}


def _v(name: str, rows: list[dict]) -> float:
    """分母为 0 时记 0（与报告里的历史口径一致）。"""
    v = METRICS[name].value(rows)
    return 0.0 if v is None else v


def _rate(rows: list[dict], pred) -> float:
    return sum(1 for r in rows if pred(r)) / len(rows) if rows else 0.0


def summarize_review(rows: list[dict]) -> dict:
    scored = [r for r in rows if _scored(r)]
    golds = [g for r in scored for g in r["gold"]]
    costs = [r["cost_usd"] for r in rows]
    lat = [r["latency_ms"] for r in rows]
    by_type = {}
    for t in RISK_TYPES:
        gs = [g for g in golds if g["type"] == t]
        by_type[t] = {
            "n": len(gs),
            "recall": _rate(gs, lambda g: g["detected"]),
            "type_ok": _rate(gs, lambda g: g["type_ok"]),
        }
    by_category = {}
    for cat in dict.fromkeys(g["category"] for g in golds if g.get("category")):
        gs = [g for g in golds if g.get("category") == cat]
        dg = [g for g in gs if g["detected"]]
        by_category[cat] = {
            "n": len(gs),
            "recall": _rate(gs, lambda g: g["detected"]),
            "type_accuracy": _rate(dg, lambda g: g["type_ok"]),
            "basis_accuracy": _rate(dg, lambda g: g["basis_ok"]),
        }
    ds = [d for r in scored for d in r.get("distractors", [])]
    distractors = {
        k: {"n": len(x), "flag_rate_hm": _rate(x, lambda d: d["flagged_hm"])}
        for k in dict.fromkeys(d["kind"] for d in ds)
        for x in [[d for d in ds if d["kind"] == k]]
    }
    v = lambda name: _v(name, rows)  # noqa: E731
    return {
        "n": len(rows),
        "detection": {
            "n_contracts": len(scored),
            "n_gold": len(golds),
            **{
                k: v(k)
                for k in (
                    "recall",
                    "type_accuracy",
                    "basis_accuracy",
                    "level_agreement",
                    "precision",
                    "precision_hm",
                    "fp_per_risky_contract",
                )
            },
            # 严格口径是后来加的：早期运行的逐项计数里没有 strict_hits，记为空而不是报错
            "precision_strict": v("precision_strict")
            if all("strict_hits" in r for r in scored)
            else None,
        },
        "clean": {
            "n": sum(1 for r in rows if _clean(r)),
            **{k: v(k) for k in ("fp_contract_rate", "fp_per_contract", "fp_contract_rate_hm")},
        },
        "perspective": {
            "favorable_n": sum(r["favorable_n"] for r in scored),
            **{k: v(k) for k in ("favorable_flag_rate", "side_accuracy")},
        },
        "human_review": {
            "borderline_n": sum(r["borderline_n"] for r in scored),
            **{
                k: v(k)
                for k in (
                    "borderline_flag_rate",
                    "borderline_human_rate",
                    "human_rate_on_gold",
                    "human_rate_all_findings",
                )
            },
        },
        "clarify": {
            "n": sum(1 for r in rows if r["clarify"]),
            **{k: v(k) for k in ("clarify_rate", "unnecessary_clarify_rate")},
        },
        "faithfulness": {k: v(k) for k in ("quote_found_rate", "invalid_citation_contract_rate")},
        "by_type": by_type,
        "by_category": by_category,
        "distractors": distractors,
        "fallback": sum(1 for r in rows if r["kind"] == "fallback"),
        "steps_mean": st.mean(r["steps"] for r in rows) if rows else 0,
        "cost": {
            "total_usd": sum(costs),
            "p50": percentile(costs, 0.5),
            "p95": percentile(costs, 0.95),
        },
        "latency_ms": {"p50": percentile(lat, 0.5), "p95": percentile(lat, 0.95)},
    }


# ---------- 接 evalkit：评测集、打分器、运行 ----------

SCORER = "contract_review"
EXPECTED_FIELDS = (
    "gold",
    "borderline",
    "favorable",
    "acceptable",
    "distractors",
    "clarify",
    "our_side",
)
# 看板上显示的自定义指标（evalkit dashboard 读运行目录里的 metrics.json）
DASHBOARD_METRICS = {
    "recall": "检出率",
    "type_accuracy": "类型判对率",
    "basis_accuracy": "依据正确率",
    "precision_hm": "精确率（高中）",
    "precision_strict": "精确率（严格）",
    "fp_contract_rate_hm": "干净合同误报率（高中）",
    "borderline_human_rate": "临界条款转人工率",
    "clarify_rate": "反问率",
}


def contract_group(c: Contract) -> str:
    if c.clarify:
        return "clarify"
    if c.favorable:
        return "favorable"
    if not c.gold:
        return "clean"
    return "borderline" if c.borderline else "risky"


def contracts_dataset(
    contracts: list[Contract], name: str = "contracts", dev_ids: set[str] | None = None
) -> Dataset:
    """合同 → evalkit 评测集。正文只记哈希（正文改了内容哈希会变，续跑会被拦下）。"""
    items = [
        Item(
            id=c.id,
            input={
                "tenant": c.tenant,
                "contract_type": c.contract_type,
                "title": c.title,
                "text_sha1": hashlib.sha1(c.text.encode()).hexdigest()[:12],
            },
            expected={k: getattr(c, k) for k in EXPECTED_FIELDS},
            tags={"contract_type": c.contract_type, "tenant": c.tenant, "group": contract_group(c)},
            split=None if dev_ids is None else ("dev" if c.id in dev_ids else "holdout"),
        )
        for c in contracts
    ]
    return Dataset(name, items)


def contract_passed(row: dict) -> bool:
    """单份合同是否"全对"：门禁和配对检验用。反问合同看是否反问；干净合同看有没有误报；
    风险合同要求风险全部检出且没有误报。"""
    bad = row["fp"] + row["favorable_flagged"] > 0
    if row["clarify"]:
        return bool(row["clarify_ok"])
    if row["clean"]:
        return not bad
    return all(g["detected"] for g in row["gold"]) and not bad


class ContractScorer:
    """evalkit 打分器：detail 是 score_contract 的逐项计数。"""

    name = SCORER

    def __init__(self, contracts: dict[str, Contract]):
        self.contracts = contracts

    def __call__(self, item: Item, output: dict) -> Score:
        row = score_contract(self.contracts[item.id], output)
        golds = row["gold"]
        value = (
            sum(g["detected"] for g in golds) / len(golds)
            if golds and not row["clarify"]
            else float(contract_passed(row))
        )
        return Score(value, contract_passed(row), row)


def _output(result) -> dict:
    """ReviewResult → 结果行里的输出：去掉完整 trace（另存为 trace 文件），只留结束方式。"""
    d = result.to_dict()
    trace = d.pop("trace", None) or {}
    return d | {"trace": {"end": trace.get("end")}}


def to_evalkit_rows(review: list[dict], variant: str = "review") -> list[dict]:
    """每份合同的逐项计数（review_results.jsonl 格式）→ evalkit 结果行。"""
    rows = []
    for r in review:
        passed = contract_passed(r)
        rows.append(
            {
                "item_id": r["id"],
                "variant": variant,
                "repeat": 0,
                "split": None,
                "tags": {"contract_type": r["contract_type"], "tenant": r["tenant"]},
                "status": "fallback" if r["kind"] == "fallback" else "ok",
                "passed": passed,
                "scores": {SCORER: {"value": float(passed), "passed": passed, "detail": r}},
                "output": None,
                "cost_usd": r["cost_usd"],
                "latency_s": r["latency_ms"] / 1000,
                "trace_id": r["trace_id"],
            }
        )
    return rows


def legacy_rows(run_dir: Path, variant: str = "review") -> list[dict]:
    """接入 evalkit 之前的运行（只有 review_results.jsonl）→ evalkit 结果行，续跑时用。"""
    return to_evalkit_rows(review_rows(run_dir), variant)


def rescore_rows(run_dir: Path, rows: list[dict], contracts: dict[str, Contract]) -> list[dict]:
    """不用运行时记下的分数，从 trace 取原始意见、按当前金标准重新判分（不调用 API）。"""
    out = []
    for row in rows:
        t = json.loads((run_dir / "traces" / f"{row['trace_id']}.json").read_text(encoding="utf-8"))
        r = t["result"] | {
            "citations": t["citations"],
            "steps": len(t["steps"]),
            "cost_usd": t["cost_usd"],
            "latency_ms": t["latency_ms"],
            "trace": {"end": t.get("end")},
        }
        out.append(score_contract(contracts[row["id"]], r) | {"trace_id": row["trace_id"]})
    return out


def evalkit_rows(run_dir: Path) -> list[dict]:
    """任意一次审查运行的 evalkit 结果行（新运行直接读，旧运行现场转换）。"""
    if (run_dir / store.RESULTS).exists():
        return store.load_rows(run_dir)
    return legacy_rows(run_dir)


def review_rows(run_dir: Path) -> list[dict]:
    """每份合同的逐项计数（review_results.jsonl 的格式）。"""
    return [
        json.loads(x)
        for x in (run_dir / "review_results.jsonl").open(encoding="utf-8")
        if x.strip()
    ]


def write_review_views(
    out: Path, rows: list[dict], contracts: list[Contract], extra: dict
) -> list[dict]:
    """由 evalkit 结果派生 review_results.jsonl、summary.json 和看板用的 metrics.json。"""
    order = {c.id: i for i, c in enumerate(contracts)}
    ok = [r for r in rows if r["status"] != "error"]
    ok.sort(key=lambda r: order.get(r["item_id"], len(order)))
    review = [r["scores"][SCORER]["detail"] | {"trace_id": r["trace_id"]} for r in ok]
    with (out / "review_results.jsonl").open("w", encoding="utf-8") as fh:
        for row in review:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = summarize_review(review) | extra
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), "utf-8")
    variants = sorted({r["variant"] for r in ok})
    metrics = {
        v: {
            label: _v(k, [x for x, r in zip(review, ok, strict=True) if r["variant"] == v])
            for k, label in DASHBOARD_METRICS.items()
        }
        for v in variants
    }
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=1), "utf-8")
    return review


def run_review_eval(
    contracts: list[Contract],
    run_one,
    tag: str,
    max_cost_usd: float = 2.0,
    meta: dict | None = None,
    resume: Path | None = None,
    concurrency: int = 1,
    dev_ids: set[str] | None = None,
    dataset_name: str = "contracts",
    variant: str = "review",
) -> Path:
    """run_one(contract) -> ReviewResult。由 evalkit 运行：逐份落盘、断点续跑、累计花费达到
    max_cost_usd 后不再开新合同（预算闸门，续跑时已花的钱也算在内）。

    resume：已有的运行目录。只补跑没有结果（或出错）的合同；接入 evalkit 之前的旧运行会先转换。
    concurrency：并行审查的合同数，默认 1（与历史运行一致，也不容易触发限流）。
    """
    by_id = {c.id: c for c in contracts}
    if resume is not None:
        out = resume
        if not (out / store.RESULTS).exists() and (out / "review_results.jsonl").exists():
            store.write_rows(out, legacy_rows(out, variant))
        resumed_from = len({r["item_id"] for r in store.load_rows(out) if r["status"] != "error"})
    else:
        out = RUNS_DIR / f"{tag}-{datetime.now():%Y%m%d-%H%M%S}"
        resumed_from = None

    def task(item: Item) -> TaskResult:
        r = run_one(by_id[item.id])
        output = _output(r)
        return TaskResult(output, output["cost_usd"], trace=r.trace, trace_id=r.trace["trace_id"])

    res = evalkit_run(
        contracts_dataset(contracts, dataset_name, dev_ids),
        [Variant(variant, task, meta or {})],
        [ContractScorer(by_id)],
        runs_dir=out.parent,
        resume=out.name,
        tag=tag,
        concurrency=concurrency,
        max_cost=max_cost_usd,
        extra_config={"meta": meta or {}},
        progress=False,
    )
    errors = [r["item_id"] for r in res.rows if r["status"] == "error"]
    done = len(res.rows) - len(errors)
    extra = (
        {"run_id": out.name, "kind": "review", "stopped_by_budget": done < len(contracts)}
        | (meta or {})
        | ({"resumed_from": resumed_from} if resume is not None else {})
        | ({"errors": errors} if errors else {})
    )
    write_review_views(out, res.rows, contracts, extra)
    return out

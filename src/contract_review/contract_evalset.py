"""合同审查评测集（evals/contracts.jsonl，由 scripts/make_contracts.py 生成，全部为构造数据）。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from contract_review.config import ROOT

CONTRACTS_PATH = ROOT / "evals" / "contracts.jsonl"
CONTRACTS_V2_PATH = ROOT / "evals" / "contracts_v2.jsonl"  # 难评测集（ADR 013）

# 风险类型 id → 中文名（10 + 1 类，ADR 007）
RISK_TYPES = {
    "penalty": "违约金过高或只约束一方",
    "liability": "免责条款或责任无上限",
    "termination": "单方解除权",
    "inspection": "检验期过短或视为合格",
    "payment": "付款周期过长或背靠背付款",
    "confidentiality": "保密条款缺失或不对等",
    "ip": "知识产权归属不利",
    "jurisdiction": "管辖不利或或裁或诉",
    "auto_renew": "自动续约",
    "deposit": "定金超过百分之二十",
    "unit_price": "单价明显高于台账历史价",
}


@dataclass
class Contract:
    id: str
    tenant: str
    contract_type: str
    template_id: str
    title: str
    our_side: str
    our_name: str
    counterparty: str
    clarify: bool
    text: str
    gold: list[dict] = field(default_factory=list)
    borderline: list[dict] = field(default_factory=list)
    favorable: list[dict] = field(default_factory=list)
    # 金标准没植入、但按租户规范报出来是对的意见（代码审查订正，见问题日志 P21）：不算误报
    acceptable: list[dict] = field(default_factory=list)
    # v2：干净合同里的干扰项（刚好合规的数值、合法免责、对我方有利条款等），报出算误报，按类别统计
    distractors: list[dict] = field(default_factory=list)
    constructed: bool = True


def load_contracts(path: Path = CONTRACTS_PATH) -> list[Contract]:
    with path.open(encoding="utf-8") as fh:
        return [Contract(**json.loads(line)) for line in fh if line.strip()]


def sample_contracts(cs: list[Contract], n: int) -> list[Contract]:
    """小样：按子集轮流取、组内补齐合同类型，保证反问、干净、临界、视角干扰和 7 类合同都覆盖。"""

    def group(c: Contract) -> str:
        if c.clarify:
            return "clarify"
        if c.favorable:
            return "favorable"
        if not c.gold:
            return "clean"
        return "borderline" if c.borderline else "risky"

    buckets: dict[str, list[Contract]] = {}
    for c in sorted(cs, key=lambda c: (c.contract_type, c.id)):
        buckets.setdefault(group(c), []).append(c)
    order = ["clarify", "favorable", "clean", "borderline", "risky"]
    out: list[Contract] = []
    seen: dict[str, int] = {}
    while len(out) < min(n, len(cs)):
        for k in order:
            items = buckets.get(k)
            if not items or len(out) >= n:
                continue
            # 组内优先取目前覆盖最少的合同类型，保证 7 类合同都进小样
            pick = min(items, key=lambda c: seen.get(c.contract_type, 0))
            items.remove(pick)
            seen[pick.contract_type] = seen.get(pick.contract_type, 0) + 1
            out.append(pick)
    return sorted(out, key=lambda c: c.id)

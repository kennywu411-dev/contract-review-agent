"""合同审查评测集（evals/contracts.jsonl，由 scripts/make_contracts.py 生成，全部为构造数据）。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from contract_review.config import ROOT

CONTRACTS_PATH = ROOT / "evals" / "contracts.jsonl"

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
    constructed: bool = True


def load_contracts(path: Path = CONTRACTS_PATH) -> list[Contract]:
    with path.open(encoding="utf-8") as fh:
        return [Contract(**json.loads(line)) for line in fh if line.strip()]

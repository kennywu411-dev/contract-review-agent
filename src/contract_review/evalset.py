"""评测集加载与校验（字段说明见 evals/README.md）。"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, fields
from pathlib import Path

from contract_review.config import ROOT, TENANTS

EVAL_DIR = ROOT / "archive" / "campus" / "evals"  # 高校场景评测集（ADR 007 后存档，仍可复现）
QUESTIONS_PATH = EVAL_DIR / "questions.jsonl"
TYPES = ("answerable", "table", "refuse")
SUBTYPES = (None, "cross_school", "version", "conflict", "clarify")
SOURCE_TYPES = ("faq", "public", "doc")
CLIENT_TENANTS = tuple(s for s in TENANTS if s != "common")
# 高校场景存档评测集里的租户（学校）id，只用于复现存档运行（ADR 007）
ARCHIVED_TENANTS = ("bnu", "upc", "njust", "lzu", "cumt", "zzu")


@dataclass
class Question:
    id: str
    tenant: str
    question: str
    type: str
    subtype: str | None
    source_type: str
    source_url: str | None
    gold: list[dict]
    answer_hint: str
    note: str = ""


def _check(q: Question) -> None:
    def bad(field: str, why: str) -> ValueError:
        return ValueError(f"{q.id}: {field} {why}")

    if q.tenant not in CLIENT_TENANTS + ARCHIVED_TENANTS:
        raise bad("tenant", f"不是学生所在学校：{q.tenant}")
    if not q.id.startswith(("common-", "national-", f"{q.tenant}-")):
        raise bad("id", f"前缀应为 {q.tenant}- 或 common-")
    if q.type not in TYPES:
        raise bad("type", f"取值非法：{q.type}")
    if q.subtype not in SUBTYPES:
        raise bad("subtype", f"取值非法：{q.subtype}")
    if q.source_type not in SOURCE_TYPES:
        raise bad("source_type", f"取值非法：{q.source_type}")
    if q.source_type in ("faq", "public") and not q.source_url:
        raise bad("source_url", "faq/public 题必须填来源")
    if q.type == "refuse" and q.gold:
        raise bad("gold", "refuse 题不应有标准出处")
    if q.type != "refuse" and not any("doc_id" in g for g in q.gold):
        raise bad("gold", "缺少文档出处")
    if q.type == "table" and not any("table" in g for g in q.gold):
        raise bad("gold", "table 题缺少表出处")
    for g in q.gold:
        if "doc_id" in g and g.get("page") is None and not g.get("clause"):
            raise bad("gold", f"{g['doc_id']} 既无页码也无条款号")
        if g.get("page") is not None and not isinstance(g["page"], int):
            raise bad("page", f"必须是整数：{g['page']!r}")


def load_questions(path: Path = QUESTIONS_PATH) -> list[Question]:
    names = {f.name for f in fields(Question)}
    out: list[Question] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if "school" in row:  # 存档文件用的旧字段名
            row["tenant"] = row.pop("school")
        unknown = set(row) - names
        if unknown:
            raise ValueError(f"{row.get('id')}: 未知字段 {sorted(unknown)}（字段名拼错？）")
        q = Question(**row)
        if q.id in seen:
            raise ValueError(f"重复的 id: {q.id}")
        seen.add(q.id)
        _check(q)
        out.append(q)
    return out


def spot_check_sample(qs: list[Question], ratio: float, seed: int = 0) -> list[Question]:
    rng = random.Random(seed)
    return sorted(rng.sample(qs, max(1, round(len(qs) * ratio))), key=lambda q: q.id)

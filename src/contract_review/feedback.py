"""试用反馈：用户对每条审查意见打标（有用 / 没用 / 不是风险 / 看不懂）。

存储和"按最后一次为准"的汇总由评测平台 evalkit.feedback 提供，这里只定义本产品的口径：
- 一个打标目标 = (会话, 审查, 第几条意见)，同一条意见可以改标；
- 只记会话 id、意见序号、标签和意见摘要（条款、类型、等级），不记合同原文；
- 主指标只算高、中等级的意见，口径见 docs/用户试用方案.md 第 5 节。
"""

from __future__ import annotations

from pathlib import Path

from evalkit import feedback as ek

LABELS = ("useful", "not_useful", "not_risk", "unclear")
MAIN_LEVELS = ("高", "中")
KEY = ("session_id", "review_id", "finding_index")


class FeedbackStore:
    def __init__(self, path: Path):
        self.path = path
        self._store = ek.FeedbackStore(path, labels=LABELS)

    def add(self, session_id: str, entry: dict) -> None:
        self._store.add({"session_id": session_id, **entry})

    def load(self) -> list[dict]:
        return self._store.load()


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, 3)


def summarize(rows: list[dict]) -> dict:
    s = ek.summarize(
        rows,
        key=KEY,
        labels=LABELS,
        positive="useful",
        negative="not_risk",
        where=lambda r: r["finding"].get("level") in MAIN_LEVELS,
        by="finding.risk_type",
    )
    groups = s["by"]["groups"] if "by" in s else {}
    return {
        "rated": s["rated"],
        "sessions": s["sessions"],
        "labels": s["labels"],
        "adoption_rate": _round(s["positive_rate"]),
        "false_positive_rate": _round(s["negative_rate"]),
        "unclear_rate": _round(s["rates"]["unclear"]),
        "by_risk_type": {
            ("?" if t == "None" else t): {
                "rated": g["rated"],
                "useful": g["useful"],
                "not_risk": g["not_risk"],
            }
            for t, g in groups.items()
        },
    }

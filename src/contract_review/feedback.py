"""试用反馈：用户对每条审查意见打标（有用 / 没用 / 不是风险 / 看不懂）。

每次打标追加一行 JSONL；同一条意见可以改标，汇总时按最后一次为准。
只记会话 id、意见序号、标签和意见摘要（条款、类型、等级），不记合同原文。
主指标只算高、中等级的意见，口径见 docs/用户试用方案.md 第 5 节。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

LABELS = ("useful", "not_useful", "not_risk", "unclear")
MAIN_LEVELS = ("高", "中")


class FeedbackStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def add(self, session_id: str, entry: dict) -> None:
        row = {"ts": time.time(), "session_id": session_id, **entry}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]


def _rate(n: int, total: int) -> float | None:
    return round(n / total, 3) if total else None


def summarize(rows: list[dict]) -> dict:
    latest: dict[tuple, dict] = {}
    for r in rows:  # 追加顺序即时间顺序，后来的覆盖先来的
        latest[(r["session_id"], r["review_id"], r["finding_index"])] = r
    main = [r for r in latest.values() if r["finding"].get("level") in MAIN_LEVELS]
    labels = {k: sum(r["label"] == k for r in main) for k in LABELS}
    by_type: dict[str, dict] = {}
    for r in main:
        t = by_type.setdefault(
            r["finding"].get("risk_type", "?"), {"rated": 0, "useful": 0, "not_risk": 0}
        )
        t["rated"] += 1
        t["useful"] += r["label"] == "useful"
        t["not_risk"] += r["label"] == "not_risk"
    return {
        "rated": len(main),
        "sessions": len({r["session_id"] for r in main}),
        "labels": labels,
        "adoption_rate": _rate(labels["useful"], len(main)),
        "false_positive_rate": _rate(labels["not_risk"], len(main)),
        "unclear_rate": _rate(labels["unclear"], len(main)),
        "by_risk_type": by_type,
    }

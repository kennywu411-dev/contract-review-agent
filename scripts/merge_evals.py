"""合并 evals/parts/*.jsonl → evals/questions.jsonl，并生成 evals/抽查清单.md（随机 20%）。"""

import json
import sqlite3

from contract_review.config import DB_PATH
from contract_review.evalset import EVAL_DIR, QUESTIONS_PATH, load_questions, spot_check_sample

# 通知类网页没有"第X条"，条款号统一写成段首原文前缀（evals/README.md）
CLAUSE_ALIASES = {
    "一（一）": "（一）增加本专科生国家奖学金",
    "一（二）": "（二）提高本专科生国家励志奖学金",
    "一（三）": "（三）提高本专科生国家助学金",
    "一（四）": "（四）增加研究生国家奖学金",
    "一（五）": "（五）提高研究生学业奖学金",
}


def main() -> None:
    rows = []
    for part in sorted((EVAL_DIR / "parts").glob("*.jsonl")):
        for line in part.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                for g in row.get("gold", []):
                    if g.get("clause") in CLAUSE_ALIASES:
                        g["clause"] = CLAUSE_ALIASES[g["clause"]]
                rows.append(row)
    rows.sort(key=lambda r: r["id"])
    QUESTIONS_PATH.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    qs = load_questions()
    conn = sqlite3.connect(DB_PATH)
    lines = [
        "# 评测集抽查清单",
        "",
        f"从 {len(qs)} 题中按 seed=0 随机抽 20%。请核对：问题是否自然、标准出处是否真能回答、",
        "答案要点是否准确。有问题的题在这里标注，修改后重新运行",
        "`uv run python scripts/merge_evals.py`。",
        "",
    ]
    for q in spot_check_sample(qs, 0.2):
        lines += [
            f"## {q.id}（{q.type} / {q.subtype or '-'} / {q.source_type}）",
            "",
            f"- 问题：{q.question}",
            f"- 答案要点：{q.answer_hint}",
        ]
        for g in q.gold:
            if "table" in g:
                lines.append(f"- 表出处：`{g['table']}` where `{g['where']}`")
                continue
            if g.get("page") is not None:
                text = "".join(
                    r[0]
                    for r in conn.execute(
                        "select text from blocks where doc_id=? and page=? order by seq",
                        (g["doc_id"], g["page"]),
                    )
                )
                where = f"第 {g['page']} 页"
            else:
                r = conn.execute(
                    "select text from blocks where doc_id=? and text like ?",
                    (g["doc_id"], g["clause"] + "%"),
                ).fetchone()
                text, where = (r[0] if r else ""), g["clause"]
            lines.append(f"- 出处：`{g['doc_id']}` {where}：{text[:200]}…")
        lines += ["- 抽查结论：", ""]
    (EVAL_DIR / "抽查清单.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"合并 {len(qs)} 题 → {QUESTIONS_PATH.relative_to(QUESTIONS_PATH.parents[1])}")


if __name__ == "__main__":
    main()

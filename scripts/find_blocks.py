"""出题用：在指定学校 + 国家层面的 blocks 里按关键词（全部包含）查原文。不经过检索器。

用法：uv run python scripts/find_blocks.py bnu 关键词1 关键词2
"""

import sqlite3
import sys

from contract_review.config import DB_PATH


def main() -> None:
    tenant, kws = sys.argv[1], sys.argv[2:]
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    sql = (
        "SELECT b.doc_id, b.page, b.seq, b.level, b.text "
        "FROM blocks b JOIN documents d USING(doc_id) "
        "WHERE d.tenant IN (?, 'common') AND b.kind != 'heading'"
        + "".join(" AND b.text LIKE ?" for _ in kws)
        + " ORDER BY d.tenant='common', b.doc_id, b.seq LIMIT 10"
    )
    for doc, page, seq, lvl, text in conn.execute(sql, [tenant] + [f"%{k}%" for k in kws]):
        print(f"{doc} p{page} s{seq} L{lvl} | {text[:150]}")


if __name__ == "__main__":
    main()

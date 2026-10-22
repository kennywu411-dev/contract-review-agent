from docpilot import store
from docpilot.quality import doc_quality


def make_db(tmp_path, pages: dict[int, str]):
    db = tmp_path / "q.db"
    conn = store.init_db(db)
    conn.execute(
        "INSERT INTO documents VALUES ('d','bnu','t','u','i','2025',NULL,'pdf','sha',?, 'now')",
        (max(pages),),
    )
    for seq, (page, text) in enumerate(pages.items()):
        conn.execute(
            "INSERT INTO blocks VALUES ('d',?,'paragraph',NULL,?,?,NULL,NULL,'[]')",
            (seq, text, page),
        )
    conn.commit()
    return db


GOOD = "第一条 为维护学校正常的教育教学秩序，保障学生合法权益，制定本规定。" * 3


def test_flags_empty_pages(tmp_path):
    db = make_db(tmp_path, {1: GOOD, 2: "12", 3: GOOD})
    q = doc_quality(db, "d")
    assert q.empty_pages == [2] and "疑似扫描页" in q.flags


def test_pages_without_blocks_count_as_empty(tmp_path):
    db = make_db(tmp_path, {1: GOOD, 3: GOOD})
    assert doc_quality(db, "d").empty_pages == [2]


def test_flags_garbled_text(tmp_path):
    db = make_db(tmp_path, {1: GOOD.replace("，", "暋") + "�"})
    q = doc_quality(db, "d")
    assert q.garbled_ratio > 0.005 and "乱码" in q.flags


def test_clean_doc_has_no_flags(tmp_path):
    q = doc_quality(make_db(tmp_path, {1: GOOD, 2: GOOD}), "d")
    assert q.flags == [] and q.n_pages == 2 and q.n_blocks == 2

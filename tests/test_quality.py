from contract_review import store
from contract_review.quality import doc_quality

GOOD = "第一条 为维护学校正常的教育教学秩序，保障学生合法权益，制定本规定。" * 3


def make_db(tmp_path, blocks: dict[int, str], layer: dict[int, int]):
    """blocks：页 → 解析出的文字；layer：页 → PDF 文字层字数。"""
    db = tmp_path / "q.db"
    conn = store.init_db(db)
    conn.execute(
        "INSERT INTO documents VALUES ('d','huaxin','t','u','i','2025',NULL,'pdf','sha',?, 'now')",
        (max(layer),),
    )
    for seq, (page, text) in enumerate(blocks.items()):
        conn.execute(
            "INSERT INTO blocks VALUES ('d',?,'paragraph',NULL,?,?,NULL,NULL,'[]')",
            (seq, text, page),
        )
    conn.executemany("INSERT INTO pages VALUES ('d',?,?)", list(layer.items()))
    conn.commit()
    return db


def test_flags_scanned_document(tmp_path):
    db = make_db(tmp_path, {1: GOOD}, {1: 300, 2: 0, 3: 0, 4: 0})
    q = doc_quality(db, "d")
    assert q.no_text_pages == [2, 3, 4] and "疑似扫描件" in q.flags


def test_single_blank_page_is_not_scanned(tmp_path):
    layer = {p: 300 for p in range(1, 11)} | {2: 0}
    q = doc_quality(make_db(tmp_path, {p: GOOD for p in range(3, 11)} | {1: GOOD}, layer), "d")
    assert q.no_text_pages == [2] and q.flags == []


def test_reports_pages_with_text_but_no_blocks(tmp_path):
    db = make_db(tmp_path, {1: GOOD}, {1: 300, 2: 400})
    q = doc_quality(db, "d")
    assert q.unparsed_pages == [2]


def test_flags_garbled_text(tmp_path):
    db = make_db(tmp_path, {1: GOOD.replace("，", "暋") + "�"}, {1: 300})
    q = doc_quality(db, "d")
    assert q.garbled_ratio > 0.005 and "乱码" in q.flags


def test_clean_doc_has_no_flags(tmp_path):
    q = doc_quality(make_db(tmp_path, {1: GOOD, 2: GOOD}, {1: 300, 2: 300}), "d")
    assert q.flags == [] and q.n_pages == 2 and q.n_blocks == 2


def test_html_doc_has_no_page_checks(tmp_path):
    db = tmp_path / "q.db"
    conn = store.init_db(db)
    conn.execute(
        "INSERT INTO documents VALUES ('d','huaxin','t','u','i','2025',NULL,'html','s',NULL,'n')"
    )
    conn.execute(
        "INSERT INTO blocks VALUES ('d',0,'paragraph',NULL,?,NULL,NULL,NULL,'[]')", (GOOD,)
    )
    conn.commit()
    q = doc_quality(db, "d")
    assert q.no_text_pages == [] and q.unparsed_pages == [] and q.flags == []

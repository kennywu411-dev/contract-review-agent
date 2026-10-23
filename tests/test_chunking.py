import json

import pytest

from contract_review import store
from contract_review.chunking import embed_text, load_chunks


def make_db(tmp_path, docs):
    """docs: {doc_id: (tenant, title, [(kind, level, text, page, section_path), ...])}"""
    db = tmp_path / "c.db"
    conn = store.init_db(db)
    for doc_id, (tenant, title, blocks) in docs.items():
        conn.execute(
            "INSERT INTO documents VALUES (?,?,?,'u','i','2025',NULL,'pdf','s',NULL,'n')",
            (doc_id, tenant, title),
        )
        for seq, (kind, level, text, page, path) in enumerate(blocks):
            conn.execute(
                "INSERT INTO blocks VALUES (?,?,?,?,?,?,NULL,NULL,?)",
                (doc_id, seq, kind, level, text, page, json.dumps(path, ensure_ascii=False)),
            )
    conn.commit()
    return db


T = "北京师范大学本科生学籍管理规定"
DOC = {
    "d1": (
        "huaxin",
        "北师大学生手册",
        [
            ("heading", 0, T, 10, []),
            ("heading", 2, "第一章 总则", 10, [T]),
            ("paragraph", 4, "第一条 " + "甲" * 450, 10, [T, "第一章 总则"]),
            ("paragraph", 4, "第二条 " + "乙" * 200, 11, [T, "第一章 总则"]),
            ("heading", 2, "第二章 注册", 11, [T]),
            ("paragraph", 4, "第三条 " + "丙" * 100, 12, [T, "第二章 注册"]),
            ("paragraph", 4, "第四条 " + "丁" * 100, 13, [T, "第二章 注册"]),
        ],
    ),
    "d2": ("yunqi", "中石油手册", [("paragraph", None, "无标题正文" * 30, 1, [])]),
}


@pytest.fixture
def db(tmp_path):
    return make_db(tmp_path, DOC)


def test_block_strategy_one_chunk_per_body_block(db):
    chunks = load_chunks(db, "block")
    assert len(chunks) == 5 and all(c.text and len(c.block_seqs) == 1 for c in chunks)
    assert chunks[0].chunk_id == "d1:block:0"


def test_fixed_strategy_overlap_and_no_cross_doc(db):
    chunks = [c for c in load_chunks(db, "fixed") if c.doc_id == "d1"]
    assert all(len(c.text) <= 400 for c in chunks)
    assert chunks[0].text[-80:] == chunks[1].text[:80]
    assert all(c.doc_id == "d1" for c in chunks)


def test_section_strategy_groups_under_leaf_heading(db):
    chunks = [c for c in load_chunks(db, "section") if c.doc_id == "d1"]
    second = [c for c in chunks if c.section_path[-1] == "第二章 注册"]
    assert len(second) == 1 and "第三条" in second[0].text and "第四条" in second[0].text


def test_section_strategy_splits_at_block_boundary_over_limit(db):
    chunks = [c for c in load_chunks(db, "section") if c.section_path[-1:] == ["第一章 总则"]]
    assert [c.block_seqs for c in chunks] == [[2], [3]]


def test_pages_and_block_seqs_tracked(db):
    second = next(c for c in load_chunks(db, "section") if c.section_path[-1:] == ["第二章 注册"])
    assert second.pages == [12, 13] and second.block_seqs == [5, 6]


def test_title_falls_back_to_document_title(db):
    d1 = load_chunks(db, "block")[0]
    d2 = next(c for c in load_chunks(db, "block") if c.doc_id == "d2")
    assert d1.title == T and d2.title == "中石油手册"


def test_embed_text_prefixes_section_path(db):
    c = load_chunks(db, "block")[0]
    assert embed_text(c).startswith(f"{T} > 第一章 总则\n第一条")


def test_chunks_remember_articles_of_sub_items(tmp_path):
    path = ["转专业办法", "第三章 程序"]
    db = make_db(
        tmp_path,
        {
            "h": (
                "lzu",
                "转专业办法",
                [
                    ("heading", 2, "第三章 程序", None, ["转专业办法"]),
                    ("paragraph", 4, "第十二条 申请范围如下：", None, path),
                    ("paragraph", None, "1.一年级可全校范围转专业。" + "甲" * 590, None, path),
                    ("paragraph", None, "2.二年级只能在学院内转专业。", None, path),
                    ("paragraph", 4, "第十三条 其他。", None, path),
                ],
            )
        },
    )
    chunks = load_chunks(db, "section")
    second = next(c for c in chunks if "二年级" in c.text)
    assert "第十二条" not in second.text and second.articles[0] == "第十二条"
    last = next(c for c in chunks if "第十三条" in c.text)
    assert last.articles[-1] == "第十三条"


def test_fixed_chunk_ignores_pages_and_articles_with_tiny_overlap(tmp_path):
    db = make_db(
        tmp_path,
        {
            "f": (
                "huaxin",
                "办法",
                [
                    ("paragraph", 4, "第一条 " + "甲" * 392, 1, ["办法"]),
                    ("paragraph", 4, "第二条 " + "乙" * 300, 2, ["办法"]),
                ],
            )
        },
    )
    first = load_chunks(db, "fixed")[0]
    assert first.pages == [1] and first.articles == ["第一条"]

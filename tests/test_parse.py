from pathlib import Path

import pytest

from contract_review.parse import parse_document

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def bnu():
    return parse_document(FIX / "bnu_sample.pdf", "huaxin", "pdf")


@pytest.fixture(scope="module")
def upc():
    return parse_document(FIX / "upc_sample.pdf", "yunqi", "pdf")


def test_pdf_blocks_have_pages_and_bbox(bnu, upc):
    for blocks, n in ((bnu, 5), (upc, 3)):
        assert blocks
        for b in blocks:
            assert 1 <= b.page <= n
            left, top, right, bottom = b.bbox
            assert left < right and top < bottom


def test_heading_levels(bnu):
    by_text = {b.text: b for b in bnu if b.kind == "heading"}
    assert by_text["第一部分政策法规、学校章程"].level == 1
    assert by_text["（3）京师奖学金"].level == 8


def test_table_is_markdown(upc):
    [table] = [b for b in upc if b.kind == "table"]
    assert table.page == 2 and "宝石学" in table.text and "大模型与具身智能" in table.text
    assert sum(line.startswith("|") for line in table.text.splitlines()) >= 23


def test_section_path(upc):
    i = next(i for i, b in enumerate(upc) if b.kind == "table")
    last_heading = next(b for b in reversed(upc[:i]) if b.kind == "heading")
    assert upc[i].section_path[-1] == last_heading.text


def test_page_numbers_become_labels_not_blocks(bnu, upc):
    assert not any(b.text.strip().isdigit() for b in bnu + upc)
    assert {b.page_label for b in upc if b.page == 1} == {"1"}


def test_garble_cleaned(upc):
    assert not any("暋" in b.text for b in upc)
    assert any(b.text == "第一章总则" and b.level == 2 for b in upc)


def test_broken_lines_merged(upc):
    art2 = next(b for b in upc if b.text.startswith("第二条"))
    assert art2.text.endswith("(以下简称学生)。")


def test_docx_blocks_have_no_page():
    path = Path(__file__).parents[1] / "data/raw/lzu/lzu-zhuanye-2025.docx"
    if not path.exists():
        pytest.skip("需要先运行 scripts/fetch.py")
    blocks = parse_document(path, "lzu", "docx")
    assert blocks and all(b.page is None for b in blocks)
    assert any(b.level == 4 for b in blocks)


def test_txt_lines_become_blocks(tmp_path):
    from contract_review.parse import parse_document

    p = tmp_path / "c.txt"
    p.write_text(
        "采购合同\n\n第一章 总则\n第一条 甲方应当付款。\n第二条 乙方应当交货。\n", encoding="utf-8"
    )
    blocks = parse_document(p, "c", "txt")
    art = next(b for b in blocks if b.text.startswith("第二条"))
    assert art.page is None and [s.replace("　", " ") for s in art.section_path] == ["第一章 总则"]

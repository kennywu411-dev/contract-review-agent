from pathlib import Path

from docpilot.html_parse import parse_html

FIX = Path(__file__).parent / "fixtures"


def test_html_blocks_have_no_page():
    blocks = parse_html(FIX / "national_sample.html", "national-xsglgd")
    assert blocks and all(b.page is None and b.bbox is None for b in blocks)
    assert any(b.kind == "heading" and b.text.startswith("第一章") for b in blocks)


def test_html_keeps_only_article_body():
    text = "".join(b.text for b in parse_html(FIX / "national_sample.html", "x"))
    assert "第一条" in text and "headSearchword" not in text and "无障碍" not in text


def test_html_articles_nest_under_chapters():
    blocks = parse_html(FIX / "national_sample.html", "x")
    art = next(b for b in blocks if b.text.startswith("第二十八条"))
    assert [t.replace("\u3000", " ") for t in art.section_path] == [
        "第三章 学籍管理",
        "第四节 休学与复学",
    ]
    assert art.level == 4 and art.kind == "paragraph"

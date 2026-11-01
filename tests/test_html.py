from pathlib import Path

from contract_review.html_parse import parse_html

FIX = Path(__file__).parent / "fixtures"


def test_html_blocks_have_no_page():
    blocks = parse_html(FIX / "national_sample.html", "common-xsglgd")
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


def test_void_elements_do_not_leak_footer(tmp_path):
    page = tmp_path / "p.html"
    page.write_text(
        '<html><body><div id="UCAP-CONTENT"><p><img src="x">第一条 正文。</p><hr>'
        '<p>第二条 正文二。<br></p></div><div class="footer">版权所有 页脚垃圾</div></body></html>',
        encoding="utf-8",
    )
    text = "".join(b.text for b in parse_html(page, "x"))
    assert "第一条" in text and "第二条" in text and "页脚" not in text


def test_court_gov_container(tmp_path):
    # 最高人民法院网站的正文容器是 class="txt_txt"
    page = tmp_path / "c.html"
    page.write_text(
        '<html><body><div class="nav">首页 导航</div><div class="txt_txt"><p>第五百八十五条　'
        '当事人可以约定违约金。</p></div><div class="share">分享</div></body></html>',
        encoding="utf-8",
    )
    text = "".join(b.text for b in parse_html(page, "x"))
    assert "第五百八十五条" in text and "导航" not in text and "分享" not in text

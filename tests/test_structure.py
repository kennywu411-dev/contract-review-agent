from docpilot.structure import RawItem, assemble


def test_unnumbered_parser_heading_becomes_document_title():
    blocks = assemble(
        "d",
        [
            RawItem("heading", "中国石油大学(华东)", page=1),
            RawItem("heading", "本科生学籍管理规定", page=1),
            RawItem("heading", "第一章总则", page=1),
            RawItem("paragraph", "第一条为维护学校正常的教育教学秩序。", page=1),
            RawItem("heading", "中华人民共和国学位法", page=2),
            RawItem("paragraph", "第一条为了规范学位授予工作。", page=2),
        ],
    )
    first, second = [b for b in blocks if b.level == 4]
    assert first.section_path == ["中国石油大学(华东)本科生学籍管理规定", "第一章总则"]
    assert second.section_path == ["中华人民共和国学位法"]


def test_unnumbered_paragraph_is_not_a_title():
    blocks = assemble("d", [RawItem("paragraph", "北京师范大学", page=1)])
    assert blocks[0].kind == "paragraph"


def test_toc_heading_is_not_a_title():
    blocks = assemble("d", [RawItem("heading", "目录", page=1)])
    assert blocks[0].kind == "paragraph"

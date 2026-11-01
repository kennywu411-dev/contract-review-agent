from contract_review.structure import RawItem, assemble, merge_paragraphs


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


def test_truncated_list_item_is_merged_not_heading():
    blocks = assemble(
        "d",
        merge_paragraphs(
            [
                RawItem("heading", "第三章 考核", page=1),
                RawItem("list", "2.对于实施分级教学的大学英语等课程,以分级考试确定", page=1),
                RawItem("paragraph", "课程成绩。", page=1),
                RawItem("paragraph", "第十条 其他。", page=1),
            ]
        ),
    )
    assert [b.kind for b in blocks] == ["heading", "list", "paragraph"]
    assert blocks[1].text.endswith("确定课程成绩。")
    assert blocks[2].section_path == ["第三章 考核"]


def test_numbered_line_with_clause_punctuation_is_not_heading():
    [b] = assemble("d", [RawItem("heading", "（1）学生应当按时注册，逾期", page=1)])
    assert b.kind != "heading"


def test_decimal_numbering_is_subheading():
    blocks = assemble(
        "d",
        [
            RawItem("heading", "3. 荣誉称号类奖学金", page=1),
            RawItem("heading", "3.1 个人类荣誉称号", page=1),
            RawItem("heading", "（1）三好学生", page=1),
        ],
    )
    assert blocks[2].section_path == ["3. 荣誉称号类奖学金", "3.1 个人类荣誉称号"]


def test_title_keeps_part_and_does_not_reset_on_non_titles():
    blocks = assemble(
        "d",
        [
            RawItem("heading", "第一部分 学籍管理", page=1),
            RawItem("heading", "北京师范大学本科学生学籍管理规定", page=1),
            RawItem("heading", "第一章 总则", page=1),
            RawItem("heading", "师校发〔2016〕43号", page=1),
            RawItem("heading", "证明材料粘贴处", page=2),
            RawItem("paragraph", "第一条 为规范学籍管理。", page=2),
        ],
    )
    assert blocks[-1].section_path == [
        "第一部分 学籍管理",
        "北京师范大学本科学生学籍管理规定",
        "第一章 总则",
    ]


def test_part_cover_page_navigation_keeps_only_its_own_part():
    from contract_review.structure import drop_part_navigation

    cover = [
        RawItem("heading", "第三部分学生管理", page=231),
        RawItem("heading", "第一部分政策法规、学校章程第二部分学籍、教学管理", page=231),
        RawItem("heading", "第三部分学生管理", page=231),
        RawItem("heading", "第四部分学生奖助", page=231),
        RawItem("heading", "第二部分", page=231),
        RawItem("heading", "北京师范大学学生违纪处分办法", page=232),
        RawItem("paragraph", "第一条 为维护秩序。", page=232),
    ]
    blocks = assemble("d", drop_part_navigation(cover))
    assert [b.text for b in blocks if b.level == 1] == ["第三部分学生管理"]
    assert blocks[-1].section_path == ["第三部分学生管理", "北京师范大学学生违纪处分办法"]


def test_part_tab_split_into_fragments_is_still_dropped():
    from contract_review.structure import drop_part_navigation

    items = [
        RawItem("heading", "第一部分政策法规、学校章程", page=9),
        RawItem("paragraph", "第一", page=9),
        RawItem("paragraph", "部分", page=9),
    ]
    blocks = assemble("d", drop_part_navigation(merge_paragraphs(items)))
    assert [b.text for b in blocks] == ["第一部分政策法规、学校章程"]

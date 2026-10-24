from contract_review.structure import RawItem, merge_paragraphs
from contract_review.textclean import normalize


def test_removes_garble_space_marker():
    assert normalize("第一章暋总暋则") == "第一章 总 则".replace(" ", "")


def test_removes_spaces_between_cjk_but_keeps_ascii_spacing():
    assert normalize("保障学生合法 权益，A B 卷") == "保障学生合法权益，A B 卷"
    assert normalize("普 通 高 等 学 校") == "普通高等学校"


def test_keeps_space_between_cjk_and_digits():
    assert normalize("教育部令第 41号") == "教育部令第 41号"


def test_merges_line_broken_paragraph():
    items = [
        RawItem("paragraph", "第二条本规定适用于接受普通高等学校学历教育的全日", page=1),
        RawItem("paragraph", "制本科生(以下简称学生)。", page=1),
        RawItem("paragraph", "第三条学校本科专业标准学制四年。", page=1),
    ]
    out = merge_paragraphs(items)
    assert [i.text for i in out] == [
        "第二条本规定适用于接受普通高等学校学历教育的全日制本科生(以下简称学生)。",
        "第三条学校本科专业标准学制四年。",
    ]


def test_does_not_merge_across_headings_tables_or_pages():
    items = [
        RawItem("paragraph", "根据有关规定", page=1),
        RawItem("heading", "第一章总则", page=1),
        RawItem("paragraph", "未完的一句", page=1),
        RawItem("paragraph", "下一页的续句。", page=2),
        RawItem("table", "| a |", page=2),
    ]
    assert len(merge_paragraphs(items)) == 5


def test_merged_bbox_covers_both_lines_top_left_origin():
    items = [
        RawItem("paragraph", "上半句", page=1, bbox=(20, 100, 290, 112)),
        RawItem("paragraph", "下半句。", page=1, bbox=(20, 114, 135, 126)),
    ]
    assert merge_paragraphs(items)[0].bbox == (20, 100, 290, 126)


def test_strip_garble_keeps_table_spacing():
    from contract_review.textclean import strip_garble

    assert strip_garble("| 备暋注 | 学 分 |") == "| 备注 | 学 分 |"


def test_garble_mapped_to_real_characters():
    from contract_review.textclean import strip_garble

    assert strip_garble("财教暡2024暢181号") == "财教〔2024〕181号"
    assert strip_garble("等级为“B暠及以上") == "等级为“B”及以上"
    assert normalize("第一章暋总暋则") == "第一章总则"

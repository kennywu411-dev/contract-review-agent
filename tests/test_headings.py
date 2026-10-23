import pytest

from contract_review.headings import heading_level


@pytest.mark.parametrize(
    "text,level",
    [
        ("第一部分 政策法规、学校章程", 1),
        ("第  四  部分 学生奖助", 1),
        ("第一章 总则", 2),
        ("第一章暋总暋则", 2),
        ("第三节 执行", 3),
        ("第十二条 学生应当按时注册。", 4),
        ("一、增加高等教育阶段国家奖学金名额", 5),
        ("（一）增加本专科生国家奖学金奖励名额", 6),
        ("(二)单项奖学金", 6),
        ("2. 专项类奖学金", 7),
        ("1.学习优秀奖学金", 7),
        ("（3）京师奖学金", 8),
        ("北京师范大学", None),
        ("2025年学生手册", None),
    ],
)
def test_heading_level(text, level):
    assert heading_level(text) == level


def test_decimal_numbering():
    assert heading_level("3.1 个人类荣誉称号") == 9
    assert heading_level("2025.9 发布") is None or heading_level("2025.9 发布") != 9


def test_civil_code_book_levels():
    # 民法典：编 > 分编 > 章，编号与"部分"之类不冲突
    assert heading_level("第三编　合同") == 10
    assert heading_level("第一分编　通则") == 11


def test_book_ranks_above_chapter():
    from contract_review.structure import _rank

    assert _rank(10) < _rank(11) < _rank(2)

import pytest

from docpilot.verify import extract_numbers, numbers_supported


@pytest.mark.parametrize(
    "text,nums",
    [
        ("奖励标准为每生每年10000元", {"10000"}),
        ("按学生数的15%评定，3000元/人", {"15", "3000"}),
        ("单次休学一般一年，累计不超过两年", {"1", "2"}),
        ("最长学习年限为六年（含休学）", {"6"}),
        ("1万元", {"10000"}),
        ("10,000元", {"10000"}),
        ("绩点3.5及以上", {"3.5"}),
        ("第十二条规定", set()),
        ("财教〔2024〕181号", set()),
        ("没有数字", set()),
    ],
)
def test_extract_numbers(text, nums):
    assert extract_numbers(text) == nums


def test_chinese_numerals_normalized():
    assert numbers_supported("累计不超过两年", ["休学累计不得超过2年"])


def test_numbers_must_appear_in_sources():
    assert not numbers_supported("一等奖学金5000元", ["一等奖学金4000元"])
    assert numbers_supported("一等奖学金5000元", ["……", "一等 5000 元/人"])


def test_numbers_checked_against_table_rows():
    rows = [["bnu", "京师奖学金", "一等", 5000, 10]]
    assert numbers_supported("京师一等5000元，比例10%", [], [rows])
    assert not numbers_supported("京师一等6000元", [], [rows])

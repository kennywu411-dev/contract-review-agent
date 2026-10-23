from docpilot.prompts import system_prompt


def test_prompt_lists_only_own_and_national_docs():
    p = system_prompt("bnu")
    assert "北京师范大学本科生学生手册" in p and "普通高等学校学生管理规定" in p
    assert "中国石油大学" not in p.split("## 可检索的文件")[1].split("##")[0]


def test_prompt_contains_table_schema_and_rules():
    p = system_prompt("upc")
    for s in (
        "scholarships",
        "national_aid",
        "school",
        "final_answer",
        "ask_clarification",
        "拒答",
    ):
        assert s in p


def test_prompt_is_stable_for_caching():
    assert system_prompt("lzu") == system_prompt("lzu")

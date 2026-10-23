from contract_review.prompts import system_prompt


def test_prompt_lists_common_docs():
    p = system_prompt("huaxin")
    assert "民法典" in p.split("## 可检索的文件")[1].split("##")[0]


def test_prompt_is_stable_for_caching():
    assert system_prompt("huaxin") == system_prompt("huaxin")

from contract_review.eval_review_d4 import score_review_d4, summarize_review_d4
from contract_review.review_verify import VerifiedReview


def f(human=False, flags=None, revised_from=None):
    x = {"reason": "r", "needs_human_review": human, "citations": ["C1"]}
    if flags:
        x["verify_flags"] = flags
    if revised_from:
        x["revised_from"] = revised_from
    return x


def test_score_counts_interception():
    raw = [f(), f(), f(), f(human=True)]
    ver = VerifiedReview(
        findings=[
            f(),  # 有依据、未标记
            f(human=True, flags=["unsupported"]),  # 无依据、被拦截
            f(revised_from="r0"),  # 改写后有依据
            f(human=True),  # 模型自己标了转人工
        ],
        flagged=1,
        revised=1,
    )
    judgment = {
        "findings": [
            {"index": 0, "grounded": True, "suggestion_score": 5},
            {"index": 1, "grounded": False, "suggestion_score": 2},
            {"index": 2, "grounded": False, "suggestion_score": 4},
            {"index": 3, "grounded": False, "suggestion_score": 3},
        ],
        "revised": [{"index": 2, "grounded": True}],
    }
    row = score_review_d4("goods-r1", raw, ver, judgment, unretrieved_raw=1)
    assert row["n"] == 4 and row["raw_ungrounded"] == 3
    # 校验后：0 有依据；1 无依据但转人工；2 改写后有依据；3 无依据但模型已转人工
    assert row["after_ungrounded_unflagged"] == 0
    assert row["raw_ungrounded_unflagged"] == 2  # 0..2 里 1、2 无依据且原本没转人工
    assert row["false_flags"] == 0 and row["suggestion_scores"] == [5, 2, 4, 3]


def test_summary_rates():
    a = {
        "id": "a",
        "n": 4,
        "raw_ungrounded": 2,
        "raw_ungrounded_unflagged": 2,
        "after_ungrounded_unflagged": 1,
        "grounded_raw": 2,
        "false_flags": 1,
        "flagged": 2,
        "revised": 1,
        "repaired": 0,
        "unretrieved_raw": 1,
        "suggestion_scores": [5, 4, 3, 2],
        "verify_cost_usd": 0.001,
        "judge_cost_usd": 0.02,
    }
    s = summarize_review_d4([a])
    assert s["findings"] == 4
    assert s["hallucination"]["raw_rate"] == 0.5
    assert s["hallucination"]["raw_unflagged_rate"] == 0.5
    assert s["hallucination"]["after_unflagged_rate"] == 0.25
    assert s["hallucination"]["false_flag_rate"] == 0.5
    assert s["unretrieved_ref_rate"] == 0.25
    assert s["suggestion"]["mean"] == 3.5 and s["suggestion"]["ge4_rate"] == 0.5

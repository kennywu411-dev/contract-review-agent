"""分析一次 agent 评测：延迟拆分（模型 vs 各工具）、失败题清单。

用法：uv run python scripts/analyze_agent.py runs/<run_id>
"""

import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

from contract_review.evalset import load_questions


def main() -> None:
    run = Path(sys.argv[1])
    qs = {q.id: q for q in load_questions()}
    rows = [json.loads(line) for line in (run / "agent_results.jsonl").open(encoding="utf-8")]
    llm_ms, tool_ms, n_llm = [], defaultdict(list), []
    usage = defaultdict(int)
    for r in rows:
        t = json.loads((run / "traces" / f"{r['trace_id']}.json").read_text(encoding="utf-8"))
        llm_ms.append(sum(s["llm_ms"] for s in t["steps"]))
        n_llm.append(len(t["steps"]))
        per = defaultdict(float)
        for s in t["steps"]:
            for k, v in s["usage"].items():
                usage[k] += v
            for c in s["tool_calls"]:
                per[c["name"]] += c.get("ms", 0)
        for k, v in per.items():
            tool_ms[k].append(v)
    print(
        f"模型调用合计：均值 {st.mean(llm_ms) / 1000:.1f}s，中位 {st.median(llm_ms) / 1000:.1f}s，"
        f"平均 {st.mean(n_llm):.1f} 次"
    )
    for k, v in tool_ms.items():
        print(f"  {k}：出现 {len(v)} 题，均值 {st.mean(v) / 1000:.2f}s")
    total_in = (
        usage["input_tokens"]
        + usage["cache_read_input_tokens"]
        + usage["cache_creation_input_tokens"]
    )
    print(
        f"token：输入 {total_in}（缓存读 {usage['cache_read_input_tokens'] / total_in:.0%}，"
        f"缓存写 {usage['cache_creation_input_tokens'] / total_in:.0%}），"
        f"输出 {usage['output_tokens']}"
    )
    print("\n失败题：")
    for r in rows:
        q = qs[r["qid"]]
        bad = []
        if q.type == "refuse" and not r["correct_refuse"]:
            bad.append(f"该拒答却{r['kind']}")
        elif q.type != "refuse" and q.subtype != "clarify":
            if r["kind"] != "answer":
                bad.append(f"误{r['kind']}")
            elif not r["cite_hit"]:
                bad.append("引用未命中")
            if q.type == "table" and not r["used_sql"]:
                bad.append("未查表")
        elif q.subtype == "clarify" and r["kind"] != "clarify":
            bad.append(f"该反问却{r['kind']}")
        if r["invalid_citations"]:
            bad.append(f"无效引用{r['invalid_citations']}")
        if bad:
            head = f"- {r['qid']} [{q.type}/{q.subtype}] {'；'.join(bad)}"
            print(f"{head}｜{q.question}｜trace {r['trace_id']}")


if __name__ == "__main__":
    main()

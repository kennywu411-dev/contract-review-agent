"""长对话评测重算（问题日志 P23）：从 trace 取回每轮回答，按当前判分规则重新打分，不调用 API。

用法：uv run python scripts/rescore_longdialog.py runs/<长对话运行目录>
原 summary.json 另存为 summary_original.json，
再写入重算后的 summary.json 和 longdialog_results.jsonl。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from contract_review.eval_retrieval import RUNS_DIR
from contract_review.eval_review_longdialog import build_long_dialogs, score_turn, summarize_long


def main() -> None:
    run = Path(sys.argv[1])
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    dialogs = {
        d["contract_id"]: d
        for d in build_long_dialogs(RUNS_DIR / summary["source_run"], summary["dialogs"])
    }
    rows = [json.loads(x) for x in (run / "longdialog_results.jsonl").open(encoding="utf-8")]
    changed = 0
    for r in rows:
        trace = json.loads((run / "traces" / f"{r['trace_id']}.json").read_text(encoding="utf-8"))
        expect = dialogs[r["contract_id"]]["turns"][r["turn"] - 1]["expect"]
        ok = score_turn(expect, trace["result"] | {"kind": r["kind"]})
        changed += ok != r["ok"]
        r["ok"] = ok
    mem_keys = ("cost_usd", "compressions", "backfilled", "compress_errors")
    mem = {
        m: {k: summary[m][k] for k in mem_keys}
        for m in ("full", "window", "compress")
        if m in summary
    }
    if not (run / "summary_original.json").exists():
        shutil.copy(run / "summary.json", run / "summary_original.json")
    new = (
        summary | summarize_long(rows, mem) | {"rescored": True, "rescored_changed_turns": changed}
    )
    (run / "summary.json").write_text(json.dumps(new, ensure_ascii=False, indent=1), "utf-8")
    (run / "longdialog_results.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8"
    )
    print(f"改判 {changed} 轮")
    for m in mem:
        s = new[m]
        print(
            m,
            {
                k: round(s[k], 3) if isinstance(s[k], float) else s[k]
                for k in (
                    "ok",
                    "early_ok",
                    "recent_ok",
                    "direct_ok",
                    "flip_ok",
                    "input_tokens_mean",
                    "history_chars_mean",
                    "total_cost_usd",
                )
            },
        )


if __name__ == "__main__":
    main()

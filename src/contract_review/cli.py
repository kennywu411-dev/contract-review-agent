"""命令行入口：uv run cra ingest。"""

from __future__ import annotations

import argparse
import time

from contract_review.campus_db import build_campus_db
from contract_review.config import CAMPUS_DB, DATA_DIR, DB_PATH, ROOT
from contract_review.quality import DocQuality, doc_quality
from contract_review.sources import fetch_all, load_sources, write_lock
from contract_review.store import ingest

REPORT_PATH = ROOT / "docs" / "解析质量.md"


def _pages(pages: list[int]) -> str:
    return (",".join(map(str, pages[:10])) + ("…" if len(pages) > 10 else "")) or "-"


def render_report(
    rows: list[tuple[str, str, DocQuality]], missing: list[str], failed: dict[str, str]
) -> str:
    lines = [
        "# 解析质量报告",
        "",
        "由 `uv run cra ingest` 生成，请勿手改。",
        "",
        "无文字页：PDF 文字层为空（空白页、图片页）；",
        "未解析页：有文字层但没解析出块（多为目录页）。",
        "",
        "| 学校 | doc_id | 页数 | 块数 | 标题 | 表格 | 无文字页 | 未解析页 | 乱码比例 | 标记 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for school, doc_id, q in rows:
        lines.append(
            f"| {school} | {doc_id} | {q.n_pages or '-'} | {q.n_blocks} | {q.n_headings} | "
            f"{q.n_tables} | {_pages(q.no_text_pages)} | {_pages(q.unparsed_pages)} | "
            f"{q.garbled_ratio:.2%} | {'、'.join(q.flags) or '-'} |"
        )
    total = sum(q.n_blocks for _, _, q in rows)
    pages = sum(q.n_pages or 0 for _, _, q in rows)
    lines += ["", f"合计：{len(rows)} 份文档，{pages} 页，{total} 个块。"]
    if missing:
        lines += ["", f"缺失（未入库）：{'、'.join(missing)}"]
    if failed:
        lines += ["", "解析失败（未入库）："] + [f"- {k}：{v}" for k, v in failed.items()]
    return "\n".join(lines) + "\n"


def cmd_ingest(args: argparse.Namespace) -> None:
    sources = load_sources()
    fetched = fetch_all(sources)
    write_lock(fetched)
    if fetched.failed:
        print(f"下载失败：{'、'.join(fetched.failed)}")
    if fetched.missing_manual:
        print(f"需手动下载：{'、'.join(fetched.missing_manual)}（见 data/sources.toml）")
    t0 = time.perf_counter()
    report = ingest(load_sources(), DB_PATH, force=args.force)
    print(
        f"解析 {len(report.parsed)}，未变 {len(report.unchanged)}，缺失 {len(report.missing)}，"
        f"失败 {len(report.failed)}，"
        f"耗时 {time.perf_counter() - t0:.1f}s"
    )
    present = [s for s in sources if s.doc_id in report.parsed + report.unchanged]
    rows = [(s.school, s.doc_id, doc_quality(DB_PATH, s.doc_id)) for s in present]
    REPORT_PATH.write_text(render_report(rows, report.missing, report.failed), encoding="utf-8")
    print(f"质量报告已写入 {REPORT_PATH.relative_to(ROOT)}")
    counts = build_campus_db(DATA_DIR / "structured", CAMPUS_DB)
    print(
        f"结构化表库 {CAMPUS_DB.relative_to(ROOT)}："
        + "，".join(f"{t} {n} 行" for t, n in counts.items())
    )


INDEX_DIR = DATA_DIR / "index"


def _retriever(strategy: str, rerank: bool = True):
    from contract_review.chunking import load_chunks
    from contract_review.embed import BgeM3Encoder
    from contract_review.rerank import BgeReranker
    from contract_review.retrieve import Retriever

    reranker = BgeReranker() if rerank else None
    return Retriever(load_chunks(DB_PATH, strategy), BgeM3Encoder(), reranker, INDEX_DIR)


def cmd_index(args: argparse.Namespace) -> None:
    from contract_review.chunking import STRATEGIES, load_chunks
    from contract_review.embed import BgeM3Encoder
    from contract_review.retrieve import Retriever

    encoder = BgeM3Encoder()
    for strategy in args.strategy or STRATEGIES:
        t0 = time.perf_counter()
        chunks = load_chunks(DB_PATH, strategy)
        Retriever(chunks, encoder, None, INDEX_DIR)
        print(f"{strategy}: {len(chunks)} 个 chunk，{time.perf_counter() - t0:.1f}s", flush=True)


def cmd_search(args: argparse.Namespace) -> None:
    retr = _retriever(args.strategy)
    res = retr.search(args.query, args.school, k=args.k, mode=args.mode)
    if res.school_mismatch:
        print(f"注意：问题提到了 {res.school_mismatch}，只检索了 {args.school} 和通用层")
    for h in res.hits:
        c = h.chunk
        print(
            f"{h.score:.3f}  {c.doc_id} 第{','.join(map(str, c.pages)) or '-'}页  "
            f"{' > '.join(c.section_path[-2:])}\n       {c.text[:80]}"
        )
    print({k: round(v) for k, v in res.latency_ms.items()})


def cmd_eval_retrieval(args: argparse.Namespace) -> None:
    from contract_review.chunking import STRATEGIES, load_chunks
    from contract_review.embed import BgeM3Encoder
    from contract_review.eval_retrieval import run_retrieval_eval, stratified_sample
    from contract_review.evalset import load_questions
    from contract_review.rerank import BgeReranker
    from contract_review.retrieve import MODES, Retriever

    qs = load_questions()
    if args.sample:
        qs = stratified_sample(qs, args.sample)
    encoder, reranker = BgeM3Encoder(), BgeReranker()
    strategies = args.strategy or list(STRATEGIES)
    retrievers = {
        s: Retriever(load_chunks(DB_PATH, s), encoder, reranker, INDEX_DIR) for s in strategies
    }
    out = run_retrieval_eval(retrievers, qs, args.mode or list(MODES), args.tag, args.pool)
    import json

    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    print(f"{out.relative_to(ROOT)}：{len(qs)} 题，跨校泄漏 {summary['_meta']['leaks']}")
    print(f"{'配置':28} {'R@1':>6} {'R@5':>6} {'R@10':>6} {'MRR':>6} {'ms':>7}")
    for config, s in summary.items():
        if config.startswith("_"):
            continue
        o = s["overall"]
        print(
            f"{config:28} {o['recall@1']:6.1%} {o['recall@5']:6.1%} {o['recall@10']:6.1%} "
            f"{o['mrr']:6.3f} {s['latency_ms_mean']:7.0f}"
        )


def print_result(r) -> None:
    for st in r.trace["steps"]:
        calls = "、".join(
            f"{c['name']}({next(iter(c['input'].values()), '') if c['input'] else ''})"[:60]
            for c in st["tool_calls"]
        )
        u = st["usage"]
        print(
            f"  第{st['step']}步 {calls or st['stop_reason']}  "
            f"in={u['input_tokens']} cache_read={u['cache_read_input_tokens']} "
            f"out={u['output_tokens']}  {st['llm_ms']:.0f}ms"
        )
    if r.kind == "clarify":
        print(f"反问：{r.clarification}")
    elif r.kind == "refuse":
        print(f"拒答：{r.refuse_reason}")
    elif r.kind == "fallback":
        print(f"未完成（{r.trace['end']}），按拒答处理")
    for sent in r.sentences:
        print(f"- {sent['text']} {''.join(f'[{c}]' for c in sent['citations'])}")
    used = {c for sent in r.sentences for c in sent["citations"]}
    for cid in sorted(used, key=lambda x: (x[0], int(x[1:]) if x[1:].isdigit() else 0)):
        c = r.citations.get(cid)
        if not c:
            print(f"  [{cid}] 不存在（无效引用）")
        elif c["kind"] == "doc":
            where = (
                f"第{','.join(map(str, c['pages']))}页" if c["pages"] else "、".join(c["articles"])
            )
            print(f"  [{cid}] 《{c['title']}》{where}")
        else:
            print(f"  [{cid}] 表 {c['sql']}")
    print(f"共 {r.steps} 步，${r.cost_usd:.5f}，{r.latency_ms / 1000:.1f}s")


def print_verified(v) -> None:
    label = {"answer": "回答", "partial": "部分回答", "refuse": "拒答", "clarify": "反问"}
    err = f"（校验器出错：{v.verifier_error}）" if v.verifier_error else ""
    print(f"\n== 校验后：{label.get(v.kind, v.kind)}{err}")
    if v.kind == "refuse":
        print(f"  {v.refuse_reason}")
    if v.kind == "clarify":
        print(f"  {v.clarification}")
    if v.unanswered and v.kind == "partial":
        print(f"  答不了的部分：{v.unanswered}")
    for s in v.kept:
        print(f"  - {s['text']} {''.join(f'[{c}]' for c in s['citations'])}")
    for d in v.dropped:
        print(f"  ✗ 已删除（{d['reason']}）：{d['sentence']['text']}")
    print(f"  校验花费 ${v.cost_usd:.5f}，{v.latency_ms / 1000:.1f}s")


def cmd_ask(args: argparse.Namespace) -> None:
    from contract_review.datapilot_client import make_backend
    from contract_review.llm import HAIKU, AnthropicLLM
    from contract_review.pipeline import answer_question
    from contract_review.tools import ToolBox
    from contract_review.trace import save_trace
    from contract_review.verify import HaikuVerifier

    retr, sql = _retriever("fixed"), make_backend()
    try:
        r, v = answer_question(
            args.question,
            args.school,
            AnthropicLLM(args.model),
            ToolBox(args.school, retr, sql),
            HaikuVerifier(AnthropicLLM(HAIKU)),
        )
    finally:
        if hasattr(sql, "close"):
            sql.close()
    print_result(r)
    print_verified(v)
    print(f"trace：{save_trace(r).relative_to(ROOT)}（查表后端：{r.trace['sql_backend']}）")


def cmd_link(_: argparse.Namespace) -> None:
    from contract_review.datapilot_client import link_datapilot

    print(f"已链接：{link_datapilot()}")


def cmd_eval_agent(args: argparse.Namespace) -> None:
    import json

    from contract_review.agent import run_agent
    from contract_review.datapilot_client import make_backend
    from contract_review.eval_agent import run_agent_eval
    from contract_review.eval_retrieval import stratified_sample
    from contract_review.evalset import load_questions
    from contract_review.llm import make_llm
    from contract_review.tools import ToolBox

    qs = load_questions()
    if args.sample:
        qs = stratified_sample(qs, args.sample)
    retr, sql, llm = _retriever("fixed"), make_backend(), make_llm(args.model)
    try:
        out = run_agent_eval(
            qs,
            lambda q: run_agent(
                q.question, q.school, llm, ToolBox(q.school, retr, sql), cache=args.cache
            ),
            args.tag,
            args.max_cost,
        )
    finally:
        if hasattr(sql, "close"):
            sql.close()
    s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    a, r = s["answerable"], s["refuse"]
    print(f"{out.relative_to(ROOT)}：{s['n']} 题，后端 {getattr(sql, 'name', '?')}")
    print(
        f"可回答 {a['n']}：作答 {a['answer_rate']:.1%}  误拒 {a['false_refusal_rate']:.1%}  "
        f"反问 {a['clarify_rate']:.1%}  引用命中 {a['cite_hit_rate']:.1%}  "
        f"无效引用 {a['invalid_citation_rate']:.1%}"
    )
    print(
        f"查表题路由 {s['table']['route_rate']:.1%}（{s['table']['n']}）  "
        f"该拒答 {r['correct_refuse_rate']:.1%}（{r['n']}）  "
        f"反问题 {s['clarify']['clarify_rate']:.1%}（{s['clarify']['n']}）  "
        f"fallback {s['fallback']}"
    )
    print(
        f"花费 ${s['cost']['total_usd']:.4f}（P50 ${s['cost']['p50']:.5f} / P95 "
        f"${s['cost']['p95']:.5f}）  耗时 P50 {s['latency_ms']['p50'] / 1000:.1f}s / "
        f"P95 {s['latency_ms']['p95'] / 1000:.1f}s  平均 {s['steps_mean']:.1f} 步"
    )


def cmd_eval_d4(args: argparse.Namespace) -> None:
    import json

    from contract_review.datapilot_client import make_backend
    from contract_review.eval_retrieval import stratified_sample
    from contract_review.evalset import load_questions
    from contract_review.judge import Judge, run_d4_eval
    from contract_review.llm import HAIKU, SONNET, AnthropicLLM
    from contract_review.pipeline import answer_question
    from contract_review.tools import ToolBox
    from contract_review.verify import HaikuVerifier

    qs = load_questions()
    if args.sample:
        qs = stratified_sample(qs, args.sample)
    retr, sql = _retriever("fixed"), make_backend()
    llm, verifier = AnthropicLLM(args.model), HaikuVerifier(AnthropicLLM(HAIKU))
    judge = Judge(AnthropicLLM(SONNET, fallbacks=True))
    try:
        out = run_d4_eval(
            qs,
            lambda q: answer_question(
                q.question, q.school, llm, ToolBox(q.school, retr, sql), verifier
            ),
            judge,
            args.tag,
            args.max_cost,
        )
    finally:
        if hasattr(sql, "close"):
            sql.close()
    s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    print(
        f"{out.relative_to(ROOT)}：{s['n']} 题"
        + ("（预算闸门提前停止）" if s["stopped_by_budget"] else "")
    )
    for name, key in [("引用准确率", "citation_accuracy"), ("幻觉率", "hallucination_rate")]:
        print(f"{name}：原始 {s[key]['raw']:.1%} → 校验后 {s[key]['verified']:.1%}")
    a, r = s["answerable"], s["refuse"]
    print(
        f"答案正确（可回答 {a['n']}）：原始 {a['correct']['raw']:.1%} → 校验后 "
        f"{a['correct']['verified']:.1%}；含部分正确 {a['correct_or_partial']['verified']:.1%}；"
        f"误拒 {a['false_refusal']['raw']:.1%} → {a['false_refusal']['verified']:.1%}"
    )
    print(
        f"拒答正确（{r['n']}）：严格 {r['strict_rate']['raw']:.1%} → "
        f"{r['strict_rate']['verified']:.1%}；"
        f"宽口径 {r['lenient_rate']['raw']:.1%} → {r['lenient_rate']['verified']:.1%}"
    )
    print(
        f"删句 {s['dropped_sentences']}，改正引用 {s['repaired_citations']}；"
        f"反问率 {s['clarify_rate']:.0%}"
    )
    print(
        f"耗时 P50 {s['latency_ms']['p50'] / 1000:.1f}s / P95 {s['latency_ms']['p95'] / 1000:.1f}s"
        f"（校验均值 {s['latency_ms']['verify_mean'] / 1000:.1f}s）；问答花费 "
        f"${s['answer_cost_usd']['total']:.3f}（P50 ${s['answer_cost_usd']['p50']:.5f}），"
        f"评判花费 ${s['judge_cost_usd']:.3f}"
    )


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    from contract_review.api import WEB_DIST, create_app

    if not WEB_DIST.exists():
        print("提示：web/out 不存在，只启动 API。构建前端：cd web && npm install && npm run build")
    print(f"DocPilot：http://{args.host}:{args.port}（首次提问会加载检索模型，约 20 秒）")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")


def cmd_reverify_d4(args: argparse.Namespace) -> None:
    """只换校验策略：复用旧运行里 agent 的原始回答，重新校验 + 评判（不重跑 agent）。"""
    import json
    from dataclasses import asdict

    from contract_review.eval_retrieval import RUNS_DIR
    from contract_review.evalset import load_questions
    from contract_review.judge import Judge, result_from_trace, run_d4_eval
    from contract_review.llm import HAIKU, SONNET, AnthropicLLM
    from contract_review.verify import HaikuVerifier, verify_result

    src = RUNS_DIR / args.source
    rows = [json.loads(x) for x in (src / "d4_results.jsonl").open(encoding="utf-8")]
    qs = {q.id: q for q in load_questions()}
    trace_of = {r["qid"]: src / "traces" / f"{r['trace_id']}.json" for r in rows}
    verifier = HaikuVerifier(AnthropicLLM(HAIKU))

    def answer_one(q):
        raw = result_from_trace(json.loads(trace_of[q.id].read_text(encoding="utf-8")))
        ver = verify_result(raw, verifier)
        raw.trace["verify"] = asdict(ver)
        # trace 里的总花费/耗时 = 原 agent 部分 + 本次新的校验（不能沿用旧校验的数字）
        raw.trace["cost_usd"] = raw.cost_usd + ver.cost_usd
        raw.trace["latency_ms"] = raw.latency_ms + ver.latency_ms
        return raw, ver

    out = run_d4_eval(
        [qs[r["qid"]] for r in rows],
        answer_one,
        Judge(AnthropicLLM(SONNET, fallbacks=True)),
        args.tag,
        args.max_cost,
    )
    print(f"{out.relative_to(ROOT)}（复用 {args.source} 的原始回答）")
    print((out / "summary.json").read_text(encoding="utf-8")[:1500])


def cmd_eval_dialog(args: argparse.Namespace) -> None:
    import json

    from contract_review.datapilot_client import make_backend
    from contract_review.eval_dialog import load_dialogs, run_dialog_eval
    from contract_review.llm import HAIKU, SONNET, AnthropicLLM
    from contract_review.tools import ToolBox
    from contract_review.verify import HaikuVerifier

    dialogs = load_dialogs()[: args.limit] if args.limit else load_dialogs()
    retr, sql = _retriever("fixed"), make_backend()
    try:
        out = run_dialog_eval(
            dialogs,
            args.mode or ["none", "full", "compress"],
            lambda school: ToolBox(school, retr, sql),
            AnthropicLLM(HAIKU),
            HaikuVerifier(AnthropicLLM(HAIKU)),
            AnthropicLLM(SONNET, fallbacks=True),
            AnthropicLLM(HAIKU),
            args.tag,
            args.max_cost,
        )
    finally:
        if hasattr(sql, "close"):
            sql.close()
    s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    print(f"{out.relative_to(ROOT)}：{s['dialogs']} 组对话，花费 ${s['spent_usd']:.3f}")
    for mode in ("none", "full", "compress"):
        if mode in s:
            m = s[mode]
            print(
                f"{mode:9} 追问正确 {m['followup_correct']:.1%}（{m['followup_n']}）  "
                f"历史 {m['history_tokens_mean']:.0f} 字  输入 {m['input_tokens_mean']:.0f} token  "
                f"压缩 {m['compressions']} 次  作答花费 ${m['cost_usd']:.3f}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(prog="cra")
    sub = parser.add_subparsers(required=True)
    p = sub.add_parser("ingest", help="下载缺失文件 → 解析入库 → 质量报告 → 结构化表库")
    p.add_argument("--force", action="store_true", help="忽略 sha256，全部重新解析")
    p.set_defaults(func=cmd_ingest)
    p = sub.add_parser("index", help="为切块策略建向量缓存（data/index/）")
    p.add_argument("--strategy", action="append", choices=["block", "fixed", "section"])
    p.set_defaults(func=cmd_index)
    p = sub.add_parser("search", help="检索调试")
    p.add_argument("query")
    p.add_argument("--school", required=True)
    p.add_argument("--strategy", default="fixed", choices=["block", "fixed", "section"])
    p.add_argument(
        "--mode", default="hybrid_rerank", choices=["bm25", "vector", "hybrid", "hybrid_rerank"]
    )
    p.add_argument("-k", type=int, default=5)
    p.set_defaults(func=cmd_search)
    p = sub.add_parser("eval-retrieval", help="检索评测：切块策略 × 检索模式")
    p.add_argument("--sample", type=int, help="按题型分层抽样 N 题先跑")
    p.add_argument("--strategy", action="append", choices=["block", "fixed", "section"])
    p.add_argument("--mode", action="append", choices=["bm25", "vector", "hybrid", "hybrid_rerank"])
    p.add_argument("--tag", default="retrieval")
    p.add_argument("--pool", type=int, default=15, help="每路召回和重排的候选数（ADR 002）")
    p.set_defaults(func=cmd_eval_retrieval)
    p = sub.add_parser("ask", help="向 agent 提问")
    p.add_argument("question")
    p.add_argument("--school", required=True, choices=["bnu", "upc", "njust", "lzu", "cumt", "zzu"])
    p.add_argument("--model", default="claude-haiku-5-5")
    p.set_defaults(func=cmd_ask)
    p = sub.add_parser("eval-agent", help="agent 评测（会调用 Claude API）")
    p.add_argument("--sample", type=int)
    p.add_argument("--model", default="claude-haiku-5-5")
    p.add_argument("--tag", default="agent")
    p.add_argument("--max-cost", type=float, default=3.0, help="累计花费上限（美元）")
    p.add_argument(
        "--cache", choices=["prefix", "system"], default="prefix", help="缓存策略对比实验"
    )
    p.set_defaults(func=cmd_eval_agent)
    p = sub.add_parser("eval-d4", help="引用校验与拒答评测（Haiku 作答+校验，Sonnet 评判）")
    p.add_argument("--sample", type=int)
    p.add_argument("--model", default="claude-haiku-5-5")
    p.add_argument("--tag", default="d4")
    p.add_argument("--max-cost", type=float, default=5.0)
    p.set_defaults(func=cmd_eval_d4)
    p = sub.add_parser("serve", help="启动 Web 界面（问答 / 评测看板 / 成本面板）")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)
    p = sub.add_parser("reverify-d4", help="复用某次 D4 运行的原始回答，只换校验策略重评")
    p.add_argument("source", help="runs/ 下的 D4 运行目录名")
    p.add_argument("--tag", default="d4-reverify")
    p.add_argument("--max-cost", type=float, default=2.5)
    p.set_defaults(func=cmd_reverify_d4)
    p = sub.add_parser("eval-dialog", help="多轮对话评测：none / full / compress 三种记忆对比")
    p.add_argument("--limit", type=int, help="只跑前 N 组对话")
    p.add_argument("--mode", action="append", choices=["none", "full", "compress"])
    p.add_argument("--tag", default="dialog")
    p.add_argument("--max-cost", type=float, default=5.0)
    p.set_defaults(func=cmd_eval_dialog)
    sub.add_parser("link-datapilot", help="把 campus 库链接进 DataPilot").set_defaults(
        func=cmd_link
    )
    args = parser.parse_args()
    args.func(args)

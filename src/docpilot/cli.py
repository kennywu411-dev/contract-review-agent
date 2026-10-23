"""命令行入口：uv run docpilot ingest。"""

from __future__ import annotations

import argparse
import time

from docpilot.campus_db import build_campus_db
from docpilot.config import CAMPUS_DB, DATA_DIR, DB_PATH, ROOT
from docpilot.quality import DocQuality, doc_quality
from docpilot.sources import fetch_all, load_sources, write_lock
from docpilot.store import ingest

REPORT_PATH = ROOT / "docs" / "解析质量.md"


def _pages(pages: list[int]) -> str:
    return (",".join(map(str, pages[:10])) + ("…" if len(pages) > 10 else "")) or "-"


def render_report(
    rows: list[tuple[str, str, DocQuality]], missing: list[str], failed: dict[str, str]
) -> str:
    lines = [
        "# 解析质量报告",
        "",
        "由 `uv run docpilot ingest` 生成，请勿手改。",
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
    from docpilot.chunking import load_chunks
    from docpilot.embed import BgeM3Encoder
    from docpilot.rerank import BgeReranker
    from docpilot.retrieve import Retriever

    reranker = BgeReranker() if rerank else None
    return Retriever(load_chunks(DB_PATH, strategy), BgeM3Encoder(), reranker, INDEX_DIR)


def cmd_index(args: argparse.Namespace) -> None:
    from docpilot.chunking import STRATEGIES, load_chunks
    from docpilot.embed import BgeM3Encoder
    from docpilot.retrieve import Retriever

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
    from docpilot.chunking import STRATEGIES, load_chunks
    from docpilot.embed import BgeM3Encoder
    from docpilot.eval_retrieval import run_retrieval_eval, stratified_sample
    from docpilot.evalset import load_questions
    from docpilot.rerank import BgeReranker
    from docpilot.retrieve import MODES, Retriever

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


def main() -> None:
    parser = argparse.ArgumentParser(prog="docpilot")
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
    p.add_argument("--pool", type=int, default=30, help="每路召回和重排的候选数")
    p.set_defaults(func=cmd_eval_retrieval)
    args = parser.parse_args()
    args.func(args)

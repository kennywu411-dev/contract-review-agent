"""Web 后端（FastAPI）：问答 SSE、PDF 原文与高亮框、评测运行与 trace 回放。

只在本机 127.0.0.1 运行。所有文件访问都按白名单（sources.toml 的 doc_id、runs/ 下的目录名）。
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from dataclasses import asdict
from functools import cache
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from contract_review.config import DB_PATH, ROOT, TENANTS
from contract_review.eval_retrieval import RUNS_DIR
from contract_review.sources import load_sources, raw_path

ClientTenant = Literal["huaxin", "yunqi", "njust", "lzu", "cumt", "ruifeng"]
_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")
WEB_DIST = ROOT / "web" / "out"


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    tenant: ClientTenant
    session_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


MAX_SESSIONS = 200  # 内存里最多保留这么多会话，超出就丢最早的（本地单机演示足够）


class RealEngine:
    """真实问答：首次请求时加载检索模型和 DataPilot 连接，之后复用。"""

    def __init__(self) -> None:
        from contract_review.cli import _retriever
        from contract_review.datapilot_client import make_backend
        from contract_review.llm import HAIKU, AnthropicLLM
        from contract_review.verify import HaikuVerifier

        self.retriever = _retriever("fixed")
        self.sql = make_backend()
        self.llm = AnthropicLLM(HAIKU)
        self.verifier = HaikuVerifier(AnthropicLLM(HAIKU))

    def ask(self, question: str, tenant: str, on_step, history: str = ""):
        from contract_review.pipeline import answer_question
        from contract_review.tools import ToolBox
        from contract_review.trace import save_trace

        raw, ver = answer_question(
            question,
            tenant,
            self.llm,
            ToolBox(tenant, self.retriever, self.sql),
            self.verifier,
            on_step=on_step,
            history=history,
        )
        save_trace(raw)
        return raw, ver

    def new_memory(self):
        from contract_review.llm import HAIKU, AnthropicLLM
        from contract_review.memory import ConversationMemory

        return ConversationMemory(mode="compress", summarizer=AnthropicLLM(HAIKU))


@cache
def _sources() -> dict:
    return {s.doc_id: s for s in load_sources()}


@cache
def _page_sizes(doc_id: str) -> dict[int, tuple[float, float]]:
    from pypdf import PdfReader

    reader = PdfReader(raw_path(_sources()[doc_id]))
    return {
        i + 1: (float(p.mediabox.width), float(p.mediabox.height))
        for i, p in enumerate(reader.pages)
    }


@cache
def _chunks(strategy: str) -> dict:
    from contract_review.chunking import load_chunks

    return {c.chunk_id: c for c in load_chunks(DB_PATH, strategy)}


def _run_kind(summary: dict) -> str:
    if summary.get("kind") == "review":
        return "review"
    if any(isinstance(v, dict) and "followup_correct" in v for v in summary.values()):
        return "dialog"
    if "_meta" in summary:
        return "retrieval"
    if "citation_accuracy" in summary:
        return "d4"
    return "agent"


def _run_dir(run: str) -> Path:
    d = RUNS_DIR / run
    if not _SAFE.match(run) or not (d / "summary.json").exists():
        raise HTTPException(404, "没有这次运行")
    return d


@cache
def _questions() -> dict:
    from contract_review.evalset import load_questions

    return {q.id: q for q in load_questions()}


def _stage_row(trace: dict) -> dict:
    tools: dict[str, float] = {}
    for st in trace.get("steps", []):
        for c in st.get("tool_calls", []):
            tools[c["name"]] = tools.get(c["name"], 0.0) + c.get("ms", 0.0)
    verify = trace.get("verify") or {}
    return {
        "trace_id": trace["trace_id"],
        "question": trace["question"],
        "tenant": trace.get("tenant", trace.get("school", "")),
        "steps": len(trace.get("steps", [])),
        "llm_ms": sum(st.get("llm_ms", 0.0) for st in trace.get("steps", [])),
        "search_ms": tools.get("search_docs", 0.0),
        "sql_ms": tools.get("run_sql", 0.0),
        "verify_ms": verify.get("latency_ms", 0.0),
        "latency_ms": trace.get("latency_ms", 0.0),
        "cost_usd": trace.get("cost_usd", 0.0),
        "cache_read": sum(
            st["usage"].get("cache_read_input_tokens", 0) for st in trace.get("steps", [])
        ),
        "cache_write": sum(
            st["usage"].get("cache_creation_input_tokens", 0) for st in trace.get("steps", [])
        ),
        "input": sum(st["usage"].get("input_tokens", 0) for st in trace.get("steps", [])),
        "output": sum(st["usage"].get("output_tokens", 0) for st in trace.get("steps", [])),
    }


def create_app(engine=None) -> FastAPI:
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(title="DocPilot")
    # 开发时前端跑在 3000 端口；cra serve 时同源，不需要 CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    state = {"engine": engine}
    app.state.engine = engine
    sessions: dict[str, tuple[str, object]] = {}  # session_id → (学校, ConversationMemory)

    def get_engine():
        if state["engine"] is None:
            state["engine"] = RealEngine()
            app.state.engine = state["engine"]
        return state["engine"]

    def memory_for(body: AskBody):
        from contract_review.memory import ConversationMemory

        if not body.session_id:
            return None
        tenant, mem = sessions.get(body.session_id, (None, None))
        if mem is None or tenant != body.tenant:  # 换学校就开新会话，不把别校的上下文带过来
            eng = get_engine()
            mem = (
                eng.new_memory() if hasattr(eng, "new_memory") else ConversationMemory(mode="full")
            )
            sessions[body.session_id] = (body.tenant, mem)
            while len(sessions) > MAX_SESSIONS:
                sessions.pop(next(iter(sessions)))
        return mem

    @app.get("/api/tenants")
    def tenants():
        return [{"id": k, "name": v} for k, v in TENANTS.items() if k != "common"]

    @app.post("/api/ask")
    async def ask(body: AskBody):
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def on_step(record):
            loop.call_soon_threadsafe(queue.put_nowait, ("step", record))

        mem = memory_for(body)

        async def work():
            from contract_review.pipeline import turn_from

            try:
                history = await asyncio.to_thread(mem.render) if mem else ""
                raw, ver = await asyncio.to_thread(
                    get_engine().ask, body.question, body.tenant, on_step, history
                )
                if mem:
                    mem.add(turn_from(raw, ver))
                await queue.put(
                    ("answer", raw.to_dict() | {"trace": {"trace_id": raw.trace.get("trace_id")}})
                )
                await queue.put(("verified", asdict(ver)))
            except Exception as e:  # 推给前端显示，不让连接挂住
                await queue.put(("error", {"message": f"{type(e).__name__}: {e}"}))
            await queue.put(("done", {}))

        async def stream():
            task = asyncio.create_task(work())
            try:
                while True:
                    name, data = await queue.get()
                    yield {"event": name, "data": json.dumps(data, ensure_ascii=False, default=str)}
                    if name == "done":
                        break
            finally:
                if not task.done():
                    # 客户端断开：取消等待中的任务，不再推送。已进入线程的那次模型调用无法打断，
                    # 会跑完这一步后结束（在线程里），结果丢弃
                    task.cancel()

        return EventSourceResponse(stream(), ping=15)

    @app.get("/api/docs/{doc_id}/pdf")
    def pdf(doc_id: str):
        src = _sources().get(doc_id)
        if src is None or src.kind != "pdf" or not raw_path(src).exists():
            raise HTTPException(404, "没有这份 PDF")
        return FileResponse(raw_path(src), media_type="application/pdf")

    @app.get("/api/docs/{doc_id}/html")
    def html_doc(doc_id: str):
        src = _sources().get(doc_id)
        if src is None or src.kind == "pdf":
            raise HTTPException(404, "没有这份文件")
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT seq, kind, level, text FROM blocks WHERE doc_id=? ORDER BY seq", (doc_id,)
        ).fetchall()
        conn.close()
        return {
            "doc_id": doc_id,
            "title": src.title,
            "url": src.url,
            "blocks": [{"seq": s, "kind": k, "level": lv, "text": t} for s, k, lv, t in rows],
        }

    @app.get("/api/highlight")
    def highlight(chunk_id: str):
        parts = chunk_id.split(":")
        if len(parts) != 3 or parts[1] not in ("block", "fixed", "section"):
            raise HTTPException(404, "没有这个片段")
        chunk = _chunks(parts[1]).get(chunk_id)
        if chunk is None:
            raise HTTPException(404, "没有这个片段")
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        marks = ",".join("?" * len(chunk.block_seqs))
        rows = conn.execute(
            f"SELECT seq, page, bbox_json FROM blocks WHERE doc_id=? AND seq IN ({marks})",
            (chunk.doc_id, *chunk.block_seqs),
        ).fetchall()
        conn.close()
        boxes = []
        sizes = _page_sizes(chunk.doc_id) if _sources()[chunk.doc_id].kind == "pdf" else {}
        for seq, page, bbox in rows:
            if page is None or not bbox:
                continue
            w, h = sizes.get(page, (0, 0))
            boxes.append(
                {
                    "seq": seq,
                    "page": page,
                    "bbox": json.loads(bbox),
                    "page_width": w,
                    "page_height": h,
                }
            )
        return {
            "doc_id": chunk.doc_id,
            "kind": _sources()[chunk.doc_id].kind,
            "pages": chunk.pages,
            "block_seqs": chunk.block_seqs,
            "articles": chunk.articles,
            "boxes": sorted(boxes, key=lambda b: (b["page"], b["seq"])),
        }

    @app.get("/api/runs")
    def runs():
        out = []
        # 按完成时间倒序：最新的运行排在前面
        for d in sorted(
            RUNS_DIR.glob("*/summary.json"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            s = json.loads(d.read_text(encoding="utf-8"))
            out.append(
                {
                    "run": d.parent.name,
                    "kind": _run_kind(s),
                    "n": s.get("n") or s.get("_meta", {}).get("n_questions"),
                }
            )
        return out

    @app.get("/api/runs/{run}")
    def run_detail(run: str):
        d = _run_dir(run)
        summary = json.loads((d / "summary.json").read_text(encoding="utf-8"))
        name = next(
            (
                n
                for n in ("d4_results.jsonl", "agent_results.jsonl", "results.jsonl")
                if (d / n).exists()
            ),
            None,
        )
        rows = [json.loads(x) for x in (d / name).open(encoding="utf-8")] if name else []
        qs = _questions()
        for r in rows:
            q = qs.get(r.get("qid"))
            if q:
                r |= {"question": q.question, "tenant": q.tenant, "answer_hint": q.answer_hint}
        return {"run": run, "kind": _run_kind(summary), "summary": summary, "rows": rows}

    @app.get("/api/runs/{run}/stages")
    def stages(run: str):
        d = _run_dir(run)
        rows = [
            _stage_row(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted((d / "traces").glob("*.json"))
        ]
        return {"run": run, "rows": rows}

    @app.get("/api/runs/{run}/traces/{trace_id}")
    def trace(run: str, trace_id: str):
        p = _run_dir(run) / "traces" / f"{trace_id}.json"
        if not _SAFE.match(trace_id) or not p.exists():
            raise HTTPException(404, "没有这条 trace")
        return json.loads(p.read_text(encoding="utf-8"))

    if WEB_DIST.exists():  # cra serve：托管已构建的前端
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app

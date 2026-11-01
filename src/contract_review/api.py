"""Web 后端（FastAPI）：合同审查与追问 SSE、法规问答 SSE、原文与高亮框、评测运行与 trace 回放。

只在本机 127.0.0.1 运行。所有文件访问都按白名单（sources.toml 的 doc_id、runs/ 下的目录名）。
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import sqlite3
import threading
from contextlib import asynccontextmanager
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
from contract_review.review_memory import ReviewMemory
from contract_review.sources import load_sources, raw_path

ClientTenant = Literal["huaxin", "yunqi", "ruifeng"]
_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")
WEB_DIST = ROOT / "web" / "out"


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    tenant: ClientTenant
    session_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ReviewBody(BaseModel):
    """首轮审查传 contract；追问传 question + session_id（合同和上一轮结果从会话里取）。"""

    contract: str | None = Field(default=None, min_length=1, max_length=20000)
    question: str | None = Field(default=None, min_length=1, max_length=2000)
    tenant: ClientTenant
    session_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


MAX_SESSIONS = 200  # 内存里最多保留这么多会话，超出就丢最早的（本地单机演示足够）


DEFAULT_REVIEW_MODE = "multi"  # 首轮审查默认多 Agent（ADR 012）；"single" 为单 Agent


class RealEngine:
    """真实问答：首次请求时加载检索模型和 DataPilot 连接，之后复用。"""

    def __init__(self, review_mode: str = DEFAULT_REVIEW_MODE) -> None:
        from contract_review.cli import _retriever
        from contract_review.datapilot_client import make_backend
        from contract_review.llm import HAIKU, AnthropicLLM
        from contract_review.verify import HaikuVerifier

        self.review_mode = review_mode
        self.retriever = _retriever("fixed")
        self.sql = make_backend()
        self.llm = AnthropicLLM(HAIKU)
        self.verifier = HaikuVerifier(AnthropicLLM(HAIKU))
        from contract_review.review_verify import ReviewVerifier

        self.review_verifier = ReviewVerifier(AnthropicLLM(HAIKU))

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

    def close(self) -> None:
        if hasattr(self.sql, "close"):
            self.sql.close()
        vectors = getattr(self.retriever, "vectors", None)
        if hasattr(vectors, "close"):
            vectors.close()
        from contract_review.milvus_index import stop_servers

        stop_servers()

    def review(
        self,
        contract: str,
        tenant: str,
        on_step,
        history: str = "",
        question: str = "",
        first_review: bool = False,
    ):
        from contract_review import review_verify, trace
        from contract_review.multi_agent import review_contract_multi
        from contract_review.review_agent import review_contract
        from contract_review.tools import ToolBox

        if (not question or first_review) and self.review_mode == "multi":
            # 首次整份审查：主审 + 专职审查员（ADR 012）；反问后用户补充的立场带给主审
            r = review_contract_multi(
                contract,
                tenant,
                self.llm,
                lambda: ToolBox(tenant, self.retriever, self.sql),
                on_step=on_step,
                hint=question,
            )
        else:  # 追问只涉及个别条款或换视角重审，单 Agent 带历史回答
            r = review_contract(
                contract,
                tenant,
                self.llm,
                ToolBox(tenant, self.retriever, self.sql),
                on_step=on_step,
                history=history,
                question=question,
            )
        ver = review_verify.verify_review(r.to_dict(), self.review_verifier, tenant, contract)
        r.trace["verify"] = asdict(ver)
        trace.save_trace(r)
        return r, ver

    def new_review_memory(self):
        from contract_review.llm import HAIKU, AnthropicLLM

        # 产品默认 8000 字才压缩：8 轮以内摘要调用的花费超过省下的 token（ADR 011）
        return ReviewMemory(mode="compress", budget_chars=8000, summarizer=AnthropicLLM(HAIKU))

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
    if summary.get("kind") in (
        "review",
        "review_d4",
        "review_dialog",
        "review_longdialog",
        "contract_retrieval",
    ):
        return summary["kind"]
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


@cache
def _contracts() -> dict:
    from contract_review.contract_evalset import CONTRACTS_V2_PATH, load_contracts

    # v1 与 v2 难评测集（ADR 013）的 id 不重叠，运行详情里两套都能配上标题
    cs = load_contracts() + (
        load_contracts(CONTRACTS_V2_PATH) if CONTRACTS_V2_PATH.exists() else []
    )
    return {c.id: c for c in cs}


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


def _error_message(e: Exception) -> str:
    """推给前端的错误文案；缺 API key 时 SDK 的英文报错对用户没用，换成中文提示。"""
    from contract_review.llm import MISSING_API_KEY_MSG, api_key_missing

    # SDK 缺 key 时抛的是 TypeError("Could not resolve authentication method ...")
    if "Could not resolve authentication method" in str(e) or (
        isinstance(e, TypeError) and api_key_missing()
    ):
        return MISSING_API_KEY_MSG
    return f"{type(e).__name__}: {e}"


def create_app(engine=None, review_mode: str = DEFAULT_REVIEW_MODE) -> FastAPI:
    from fastapi.middleware.cors import CORSMiddleware

    state = {"engine": engine}

    @asynccontextmanager
    async def lifespan(_app):
        yield
        # 关闭时回收 DataPilot MCP 子进程、Milvus 服务等
        eng = state["engine"]
        if eng is not None and hasattr(eng, "close"):
            eng.close()

    app = FastAPI(title="Contract Risk Review Agent", lifespan=lifespan)
    # 开发时前端跑在 3000 端口；cra serve 时同源，不需要 CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.state.engine = engine
    sessions: dict[str, tuple[str, object]] = {}  # session_id → (租户, ConversationMemory)

    def get_engine():
        if state["engine"] is None:
            state["engine"] = RealEngine(review_mode)
            app.state.engine = state["engine"]
        return state["engine"]

    def memory_for(body: AskBody):
        from contract_review.memory import ConversationMemory

        if not body.session_id:
            return None
        tenant, mem = sessions.get(body.session_id, (None, None))
        if mem is None or tenant != body.tenant:  # 换租户就开新会话，不把别的公司的上下文带过来
            eng = get_engine()
            mem = (
                eng.new_memory() if hasattr(eng, "new_memory") else ConversationMemory(mode="full")
            )
            sessions[body.session_id] = (body.tenant, mem)
            while len(sessions) > MAX_SESSIONS:
                sessions.pop(next(iter(sessions)))
        return mem

    review_sessions: dict[str, dict] = {}  # session_id → {tenant, contract, memory, lock, reviewed}
    app.state.review_sessions = review_sessions

    @app.get("/api/samples")
    def samples():
        """评测集里的构造合同，供演示时一键填入（不含金标准答案）。"""
        from contract_review.contract_evalset import load_contracts

        return [
            {
                "id": c.id,
                "title": c.title,
                "tenant": c.tenant,
                "our_name": c.our_name if not c.clarify else "",
                "counterparty": c.counterparty if not c.clarify else "",
                "text": c.text,
                "constructed": True,
            }
            for c in load_contracts()
        ]

    @app.post("/api/review")
    async def review(body: ReviewBody):
        sess = review_sessions.get(body.session_id or "")
        if body.question:
            sid = body.session_id or ""
            if sess is None or sess["tenant"] != body.tenant:
                raise HTTPException(400, "追问需要先在同一会话里审查一份合同")
            # 追问只针对会话里那份合同；请求里带来的 contract 不能把旧历史配到新合同上
            contract, memory = sess["contract"], sess["memory"]
            # 同一会话同时只处理一个请求：并发追问会同时改同一份记忆（审查 I4）
            if not sess["lock"].acquire(blocking=False):
                raise HTTPException(409, "这个会话上一轮还没结束，请稍候再问")
        else:
            if not body.contract:
                raise HTTPException(400, "请提供合同全文")
            contract = body.contract
            # 会话 id 由服务端签发（128 位随机，不可猜），客户端自带的 id 不采用
            sid = secrets.token_hex(16)
            eng = get_engine()
            # 多轮记忆：真实引擎超预算时摘要压缩（ADR 011）；测试用的假引擎退回全量历史
            memory = (
                eng.new_review_memory()
                if hasattr(eng, "new_review_memory")
                else ReviewMemory(mode="full")
            )
            sess = {
                "tenant": body.tenant,
                "contract": contract,
                "memory": memory,
                "lock": threading.Lock(),
                "reviewed": False,  # 是否已有一次给出意见的审查（首轮反问时还没有）
            }
            sess["lock"].acquire()
            review_sessions[sid] = sess
            while len(review_sessions) > MAX_SESSIONS:
                review_sessions.pop(next(iter(review_sessions)))
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        # 首轮反问后，用户补充立场的那一轮才是真正的首次审查（审查 M3）
        first_review = not body.question or not sess["reviewed"]
        held = [True]

        def release():
            if held[0]:
                held[0] = False
                sess["lock"].release()

        def on_step(record):
            loop.call_soon_threadsafe(queue.put_nowait, ("step", record))

        async def work():
            try:
                # 超预算时 render 会调用摘要模型，放到线程里
                history = await asyncio.to_thread(memory.render) if body.question else ""
                r, ver = await asyncio.to_thread(
                    get_engine().review,
                    contract,
                    body.tenant,
                    on_step,
                    history,
                    body.question or "",
                    first_review=first_review,
                )
                data = r.to_dict()
                data["trace"] = {"trace_id": r.trace.get("trace_id")}
                # 记忆里存校验后的意见（带转人工标记）；会话中途被淘汰也只是写进一个不再引用的对象
                turn = data | {"findings": ver.findings or r.findings}
                if body.question:
                    memory.add_followup(body.question, turn)
                else:
                    memory.add_review(turn)
                if r.kind == "review":
                    sess["reviewed"] = True
                await queue.put(("review", data))
                await queue.put(("verified", asdict(ver)))
            except Exception as e:  # 推给前端显示，不让连接挂住
                await queue.put(("error", {"message": _error_message(e)}))
            finally:
                release()
            await queue.put(("done", {}))

        async def stream():
            if not body.question:
                yield {"event": "session", "data": json.dumps({"session_id": sid})}
            task = asyncio.create_task(work())
            try:
                while True:
                    name, data = await queue.get()
                    yield {"event": name, "data": json.dumps(data, ensure_ascii=False, default=str)}
                    if name == "done":
                        break
            finally:
                if not task.done():
                    task.cancel()
                release()  # 任务还没开始就被取消时，work 的 finally 不会执行

        return EventSourceResponse(stream(), ping=15)

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
                await queue.put(("error", {"message": _error_message(e)}))
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
                    "n": s.get("n")
                    or s.get("contracts")
                    or s.get("dialogs")
                    or s.get("_meta", {}).get("n_questions"),
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
                for n in (
                    "d4_results.jsonl",
                    "agent_results.jsonl",
                    "review_results.jsonl",
                    "dialog_results.jsonl",
                    "longdialog_results.jsonl",
                    "results.jsonl",
                )
                if (d / n).exists()
            ),
            None,
        )
        rows = [json.loads(x) for x in (d / name).open(encoding="utf-8")] if name else []
        qs = _questions()
        contracts = _contracts()
        for r in rows:
            q = qs.get(r.get("qid"))
            if q:
                r |= {"question": q.question, "tenant": q.tenant, "answer_hint": q.answer_hint}
            c = contracts.get(r.get("id") or r.get("contract_id"))
            if c:
                r |= {"title": c.title, "tenant": c.tenant}
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

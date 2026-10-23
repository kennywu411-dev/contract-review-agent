"""查结构化表：优先走 DataPilot 的只读 MCP Server（stdio），没有 DataPilot 时退回本地只读 sqlite。

DataPilot 只认 data/dev_databases/<db_id>/<db_id>.sqlite，所以在那里建一个
指向 data/campus 的软链接，不改 DataPilot 的代码（`docpilot link-datapilot`）。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Protocol

from docpilot.config import CAMPUS_DB

DATAPILOT_DIR = Path(os.environ.get("DATAPILOT_DIR", Path.home() / "Documents" / "datapilot"))
DB_ID = "campus"
MAX_ROWS = 50
CALL_TIMEOUT_S = 20


class SQLBackend(Protocol):
    name: str

    def run(self, sql: str) -> dict: ...


def link_datapilot() -> Path:
    """在 DataPilot 的库目录下建 campus → 本仓库 data/campus 的软链接；已存在则不动。"""
    target = DATAPILOT_DIR / "data" / "dev_databases" / DB_ID
    if not target.is_symlink():
        target.symlink_to(CAMPUS_DB.parent.resolve(), target_is_directory=True)
    return target


_SELECT = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


def _deny_writes(action, *_):
    read_ops = (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION)
    return sqlite3.SQLITE_OK if action in read_ops else sqlite3.SQLITE_DENY


class LocalBackend:
    """降级用：mode=ro + 只放行单条 SELECT + 授权回调拒绝写操作（同 DataPilot 的三层思路）。"""

    name = "local"

    def __init__(self, db: Path = CAMPUS_DB) -> None:
        self.db = db

    def run(self, sql: str) -> dict:
        body = sql.strip().rstrip(";")
        if ";" in body or not _SELECT.match(body):
            return {"error": "rejected", "message": "只允许单条 SELECT"}
        conn = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        conn.set_authorizer(_deny_writes)
        try:
            cur = conn.execute(body)
            rows = [list(r) for r in cur.fetchmany(MAX_ROWS)]
            return {"columns": [d[0] for d in cur.description], "rows": rows}
        except sqlite3.Error as e:
            return {"error": "sql_error", "message": str(e)}
        finally:
            conn.close()


class MCPBackend:
    """后台线程里常驻一个 MCP 会话：避免每次查表都重新拉起 uv 和 Python（约 1–2 秒）。"""

    name = "datapilot-mcp"

    def __init__(self, datapilot_dir: Path | None = None) -> None:
        self.dir = datapilot_dir or DATAPILOT_DIR
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self._stop = asyncio.Event()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self.session = None
        self._task = asyncio.run_coroutine_threadsafe(self._serve(), self.loop)
        if not self._ready.wait(CALL_TIMEOUT_S * 3):
            raise TimeoutError("DataPilot MCP Server 启动超时")
        if self._error:
            raise self._error

    async def _serve(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command="uv",
            args=[
                "run",
                "-q",
                "--directory",
                str(self.dir),
                "python",
                "-m",
                "datapilot.mcp_server",
            ],
        )
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.session = session
                    self._ready.set()
                    await self._stop.wait()
        except BaseException as e:  # 启动失败要让构造函数知道
            self._error = e
            self._ready.set()

    async def _call(self, sql: str) -> dict:
        result = await self.session.call_tool(
            "run_sql", {"db_id": DB_ID, "sql": sql, "max_rows": MAX_ROWS}
        )
        data = getattr(result, "structuredContent", None)
        if isinstance(data, dict) and set(data) == {"result"}:
            data = data["result"]
        if not isinstance(data, dict):
            data = json.loads(result.content[0].text)
        return data

    def run(self, sql: str) -> dict:
        future = asyncio.run_coroutine_threadsafe(self._call(sql), self.loop)
        try:
            return future.result(CALL_TIMEOUT_S)
        except Exception as e:  # MCP 断开、超时：作为工具错误交还给模型
            return {"error": "backend_error", "message": f"{type(e).__name__}: {e}"}

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self._stop.set)
        try:
            self._task.result(10)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)


def make_backend() -> SQLBackend:
    if (DATAPILOT_DIR / "src" / "datapilot" / "mcp_server.py").exists():
        try:
            link_datapilot()
            return MCPBackend()
        except Exception:
            pass
    return LocalBackend()

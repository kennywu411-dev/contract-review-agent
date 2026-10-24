"""Milvus Lite 向量后端（可选依赖 `uv sync --extra milvus`），与 numpy 后端可切换（ADR 009）。

向量本身仍走 VectorIndex 的 sha1 缓存（不重复编码）；这里只负责把向量和租户同步进 Milvus Lite，
检索时把租户条件下推成过滤表达式，而不是取回后再删，保证前 k 个里不会被别家租户占位。
内容指纹（全部文本 sha1 + 租户）不变时直接复用集合，变了才重建。

连接方式（ADR 009）：
- 默认：在本机 127.0.0.1 起一个 Milvus Lite 服务子进程，用 URI 连接。
  Milvus Lite 3.x 是进程内纯 Python 实现，检索用 faiss（自带 libomp），和 torch 自带的 libomp
  同进程会报 OMP #15；分进程从根上隔离，连接方式也和生产环境的 Milvus Standalone 一样；
- 环境变量 MILVUS_URI：直接连外部 Milvus（如 Docker 里的 Standalone）；
- 传 .db 路径：进程内模式（测试用，不跑 torch 推理时可用）。
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from contract_review.vector import Encoder, VectorIndex

BATCH = 500
SERVER_START_TIMEOUT_S = 60
_SERVERS: dict[str, subprocess.Popen] = {}  # 数据目录 → 服务子进程


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


START_ATTEMPTS = 3


def _ready(uri: str) -> bool:
    """用一次真实调用确认是 Milvus 服务在应答（被别的程序占着的端口也能建 TCP 连接）。"""
    from pymilvus import MilvusClient

    try:
        client = MilvusClient(uri, timeout=2)
        client.list_collections()
        client.close()
        return True
    except Exception:
        return False


def _reap(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


def _ensure_server(data_dir: Path) -> str:
    """同一数据目录只起一个服务；只监听 127.0.0.1。

    先取空闲端口再交给子进程绑定，中间可能被别人抢走：子进程退出就换端口重试。
    """
    key = str(data_dir.resolve())
    proc = _SERVERS.get(key)
    if proc is not None and proc.poll() is None:
        return proc.uri  # type: ignore[attr-defined]
    for _ in range(START_ATTEMPTS):
        port = _free_port()
        uri = f"http://127.0.0.1:{port}"
        cmd = [sys.executable, "-m", "milvus_lite", "server", "--data-dir", key]
        cmd += ["--host", "127.0.0.1", "--port", str(port)]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + SERVER_START_TIMEOUT_S
        while proc.poll() is None and time.monotonic() < deadline:
            if _ready(uri) and proc.poll() is None:
                proc.uri = uri  # type: ignore[attr-defined]
                _SERVERS[key] = proc
                return uri
            time.sleep(0.2)
        _reap(proc)  # 端口被占（子进程退出）或超时：回收后换端口再试
    raise RuntimeError(f"Milvus Lite 服务没起来（试了 {START_ATTEMPTS} 次）：{key}")


def stop_servers() -> None:
    for proc in _SERVERS.values():
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _reap(proc)
    _SERVERS.clear()


atexit.register(stop_servers)
_SAFE_TENANT = re.compile(r"^[a-z0-9_]+$")


def _fingerprint(texts: list[str], tenants: list[str]) -> str:
    h = hashlib.sha1()
    for t, ten in zip(texts, tenants, strict=True):
        h.update(hashlib.sha1(t.encode("utf-8")).digest())
        h.update(ten.encode("utf-8"))
    return h.hexdigest()


class MilvusVectorIndex:
    def __init__(self, client, collection: str, rebuilt: bool) -> None:
        self.client, self.collection, self.rebuilt = client, collection, rebuilt

    @classmethod
    def build(
        cls,
        texts: list[str],
        tenants: list[str],
        encoder: Encoder,
        cache_path: Path,
        collection: str,
        uri: str | None = None,
    ) -> MilvusVectorIndex:
        from pymilvus import MilvusClient

        vectors = VectorIndex.build(texts, encoder, cache_path).vectors
        db = cache_path.parent / "milvus.db"
        meta_path = cache_path.with_suffix(".milvus.json")
        name = re.sub(r"[^A-Za-z0-9_]", "_", collection)
        uri = uri or os.environ.get("MILVUS_URI") or _ensure_server(db)
        client = MilvusClient(uri)
        fp = _fingerprint(texts, tenants)
        meta = json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else {}
        if client.has_collection(name) and meta.get("fingerprint") == fp:
            client.load_collection(name)  # 新进程里复用时集合是 released 状态，要先加载
            return cls(client, name, rebuilt=False)
        if client.has_collection(name):
            client.drop_collection(name)
        client.create_collection(name, dimension=int(vectors.shape[1]), metric_type="IP")
        rows = [
            {"id": i, "vector": vectors[i].tolist(), "tenant": tenants[i]}
            for i in range(len(texts))
        ]
        for start in range(0, len(rows), BATCH):
            client.insert(name, rows[start : start + BATCH])
        meta_path.write_text(json.dumps({"fingerprint": fp, "rows": len(rows)}), "utf-8")
        return cls(client, name, rebuilt=True)

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None

    def search(
        self, qvec: np.ndarray, k: int, allow=None, tenants: list[str] | None = None
    ) -> list[tuple[int, float]]:
        if tenants is None:
            raise ValueError("Milvus 后端需要 tenants 过滤条件")
        if not all(_SAFE_TENANT.match(t) for t in tenants):  # 拼进过滤表达式，只接受固定取值
            raise ValueError(f"非法租户：{tenants}")
        expr = "tenant in [" + ", ".join(f'"{t}"' for t in tenants) + "]"
        res = self.client.search(
            self.collection,
            data=[np.asarray(qvec, dtype=np.float32).tolist()],
            limit=k,
            filter=expr,
            search_params={"metric_type": "IP"},
        )
        return [(int(h["id"]), float(h["distance"])) for h in res[0]]

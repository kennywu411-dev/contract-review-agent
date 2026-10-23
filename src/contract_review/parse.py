"""按文件类型分派到具体解析器（ADR 001）。"""

from __future__ import annotations

from pathlib import Path

from contract_review.blocks import Block
from contract_review.html_parse import parse_html
from contract_review.pdf_parse import parse_docling
from contract_review.structure import RawItem, assemble


def parse_document(path: Path, doc_id: str, kind: str) -> list[Block]:
    if kind == "html":
        return parse_html(path, doc_id)
    if kind == "txt":
        lines = path.read_text(encoding="utf-8").splitlines()
        return assemble(doc_id, [RawItem("paragraph", t.strip()) for t in lines if t.strip()])
    if kind in ("pdf", "docx"):
        return parse_docling(path, doc_id)
    raise ValueError(f"不支持的文件类型: {kind}")

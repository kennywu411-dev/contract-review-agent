"""按文件类型分派到具体解析器（ADR 001）。"""

from __future__ import annotations

from pathlib import Path

from docpilot.blocks import Block
from docpilot.html_parse import parse_html
from docpilot.pdf_parse import parse_docling


def parse_document(path: Path, doc_id: str, kind: str) -> list[Block]:
    if kind == "html":
        return parse_html(path, doc_id)
    if kind in ("pdf", "docx"):
        return parse_docling(path, doc_id)
    raise ValueError(f"不支持的文件类型: {kind}")

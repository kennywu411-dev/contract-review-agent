"""解析结果的统一结构：一个 Block 是文档里的一个标题、段落、列表项或表格。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["heading", "paragraph", "table", "list"]


@dataclass
class Block:
    doc_id: str
    seq: int
    kind: Kind
    text: str
    level: int | None = None
    page: int | None = None  # PDF 物理页序号，从 1 开始；网页为 None
    page_label: str | None = None  # 印刷页码（如有）
    bbox: tuple[float, float, float, float] | None = None  # (l, t, r, b)，PDF 点，左上角为原点
    section_path: list[str] = field(default_factory=list)

"""PDF / DOCX 解析：docling（关闭 OCR，见 ADR 001）→ RawItem → Block。"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

from contract_review.blocks import Block
from contract_review.structure import RawItem, assemble, drop_part_navigation, merge_paragraphs
from contract_review.textclean import normalize, strip_garble

KIND_BY_LABEL = {
    "title": "heading",
    "section_header": "heading",
    "text": "paragraph",
    "paragraph": "paragraph",
    "caption": "paragraph",
    "footnote": "paragraph",
    "list_item": "list",
    "table": "table",
}
PAGE_NUMBER = re.compile(r"^\d{1,4}$")


@cache
def _converter():
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions(do_ocr=False)
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def parse_docling(path: Path, doc_id: str) -> list[Block]:
    from docling_core.types.doc import ContentLayer

    doc = _converter().convert(path).document
    layers = {ContentLayer.BODY, ContentLayer.FURNITURE}
    items: list[RawItem] = []
    labels: dict[int, str] = {}
    for item, _ in doc.iterate_items(included_content_layers=layers):
        label = str(getattr(item, "label", ""))
        kind = KIND_BY_LABEL.get(label)
        prov = item.prov[0] if getattr(item, "prov", None) else None
        page = prov.page_no if prov else None
        if kind == "table":
            text = strip_garble(item.export_to_markdown(doc))
        else:
            text = normalize(getattr(item, "text", "") or "")
        if not text:
            continue
        # 印刷页码记为 page_label，不作为正文块；其余页眉页脚丢弃
        if page is not None and PAGE_NUMBER.match(text):
            labels.setdefault(page, text)
            continue
        if kind is None or item.content_layer == ContentLayer.FURNITURE:
            continue
        bbox = None
        if prov:
            height = doc.pages[page].size.height
            bb = prov.bbox.to_top_left_origin(height)
            bbox = (round(bb.l, 1), round(bb.t, 1), round(bb.r, 1), round(bb.b, 1))
        items.append(RawItem(kind, text, page, bbox))
    blocks = assemble(doc_id, drop_part_navigation(merge_paragraphs(items)))
    for b in blocks:
        if b.page is not None:
            b.page_label = labels.get(b.page)
    return blocks

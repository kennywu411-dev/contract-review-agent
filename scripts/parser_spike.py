"""解析器选型实验：docling 在夹具上的输出概况（MinerU 见 ADR 001）。

用法：uv run --isolated --python 3.12 --with docling python scripts/parser_spike.py <pdf>...
输出 JSON 到 stdout：耗时、峰值内存、各类元素数、页码/bbox 覆盖率、样例。
"""

import json
import resource
import sys
import time
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption


def main(paths: list[str]) -> None:
    # 文字版 PDF 不需要 OCR；开 OCR 时 378 页手册 15 分钟未跑完
    opts = PdfPipelineOptions(do_ocr=False)
    conv = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    for p in paths:
        t0 = time.perf_counter()
        doc = conv.convert(p).document
        dt = time.perf_counter() - t0
        items = list(doc.iterate_items())
        kinds: dict[str, int] = {}
        with_prov = 0
        samples = []
        for item, level in items:
            label = str(getattr(item, "label", type(item).__name__))
            kinds[label] = kinds.get(label, 0) + 1
            if getattr(item, "prov", None):
                with_prov += 1
            if label in ("section_header", "title", "table") and len(samples) < 12:
                text = item.export_to_markdown(doc) if label == "table" else item.text
                page = item.prov[0].page_no if item.prov else None
                samples.append({"label": label, "level": level, "page": page, "text": text[:300]})
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6
        out = {
            "file": Path(p).name,
            "seconds": round(dt, 1),
            "peak_rss_mb": round(rss),
            "n_items": len(items),
            "kinds": kinds,
            "prov_coverage": round(with_prov / max(len(items), 1), 3),
            "garbled_暋": doc.export_to_markdown().count("暋"),
            "samples": samples,
        }
        print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])

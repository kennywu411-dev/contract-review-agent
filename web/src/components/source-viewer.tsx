"use client";

import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { type DocCitation, getJSON, type Highlight, type HtmlDoc, pdfUrl } from "@/lib/api";
import { bboxToViewport, fitScale } from "@/lib/bbox.mjs";
import { cn } from "@/lib/utils";

type PdfDoc = { numPages: number; getPage: (n: number) => Promise<PdfPage> };
type PdfPage = {
  getViewport: (o: { scale: number }) => { width: number; height: number };
  render: (o: { canvasContext: CanvasRenderingContext2D; viewport: unknown }) => { promise: Promise<void> };
};

const pdfCache = new Map<string, Promise<PdfDoc>>();

async function loadPdf(docId: string): Promise<PdfDoc> {
  if (!pdfCache.has(docId)) {
    pdfCache.set(
      docId,
      (async () => {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();
        return (await pdfjs.getDocument(pdfUrl(docId)).promise) as unknown as PdfDoc;
      })(),
    );
  }
  return pdfCache.get(docId)!;
}

function PdfPageView({ docId, page, boxes }: { docId: string; page: number; boxes: Highlight["boxes"] }) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [scale, setScale] = useState(0);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const doc = await loadPdf(docId);
        const p = await doc.getPage(page);
        const base = p.getViewport({ scale: 1 });
        const s = fitScale(base.width, wrap.current?.clientWidth ?? 600);
        const vp = p.getViewport({ scale: s * (window.devicePixelRatio || 1) });
        const c = canvas.current!;
        c.width = vp.width;
        c.height = vp.height;
        c.style.width = `${base.width * s}px`;
        c.style.height = `${base.height * s}px`;
        await p.render({ canvasContext: c.getContext("2d")!, viewport: vp }).promise;
        if (!cancelled) setScale(s);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [docId, page]);

  useEffect(() => {
    // 第一处高亮滚到视野中央
    if (scale && boxes.length) {
      const first = wrap.current?.querySelector("[data-hl]");
      first?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [scale, boxes]);

  if (error) return <p className="text-destructive p-4 text-sm">PDF 加载失败：{error}</p>;
  return (
    <div ref={wrap} className="relative w-full">
      <canvas ref={canvas} className="block w-full rounded-sm bg-white" />
      {scale > 0 &&
        boxes
          .filter((b) => b.page === page)
          .map((b) => {
            const v = bboxToViewport(b.bbox, scale);
            return (
              <div
                key={b.seq}
                data-hl
                className="pointer-events-none absolute rounded-sm bg-yellow-300/35 ring-2 ring-yellow-400/80"
                style={{ left: v.left, top: v.top, width: v.width, height: v.height }}
              />
            );
          })}
    </div>
  );
}

function HtmlDocView({ docId, seqs }: { docId: string; seqs: number[] }) {
  const [doc, setDoc] = useState<HtmlDoc | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    getJSON<HtmlDoc>(`/api/docs/${encodeURIComponent(docId)}/html`).then(setDoc).catch(() => setDoc(null));
  }, [docId]);
  useEffect(() => {
    ref.current?.querySelector("[data-hl]")?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [doc, seqs]);
  if (!doc) return <p className="text-muted-foreground p-4 text-sm">加载中…</p>;
  const set = new Set(seqs);
  return (
    <div ref={ref} className="space-y-2 p-4 text-sm leading-relaxed">
      {doc.blocks.map((b) => (
        <p
          key={b.seq}
          data-hl={set.has(b.seq) || undefined}
          className={cn(
            b.kind === "heading" && "font-semibold",
            set.has(b.seq) && "-mx-2 rounded bg-yellow-300/25 px-2 ring-1 ring-yellow-400/70",
          )}
        >
          {b.text}
        </p>
      ))}
    </div>
  );
}

export function SourceViewer({ id, citation, onClose }: { id: string; citation: DocCitation; onClose: () => void }) {
  const [hl, setHl] = useState<Highlight | null>(null);
  const [pageIdx, setPageIdx] = useState(0);
  useEffect(() => {
    setHl(null);
    setPageIdx(0);
    getJSON<Highlight>(`/api/highlight?chunk_id=${encodeURIComponent(citation.chunk_id)}`).then(setHl).catch(() => setHl(null));
  }, [citation.chunk_id]);

  const pages = hl?.pages.length ? hl.pages : citation.pages;
  const where = citation.pages.length ? `第 ${pages[pageIdx]} 页（共涉及 ${pages.length} 页）` : citation.articles.join("、");
  return (
    <aside className="bg-card flex h-full min-h-0 flex-col border-l" aria-label="引用原文">
      <div className="flex items-start gap-2 border-b p-3">
        <div className="min-w-0 flex-1">
          <p className="text-muted-foreground text-xs">[{id}] 原文</p>
          <p className="truncate text-sm font-medium" title={citation.title}>
            《{citation.title}》
          </p>
          <p className="text-muted-foreground text-xs">
            {citation.section_path.slice(1).join(" › ")} · {where}
          </p>
        </div>
        {pages.length > 1 && (
          <div className="flex items-center">
            <button aria-label="上一页" disabled={pageIdx === 0} onClick={() => setPageIdx((i) => i - 1)}
              className="hover:bg-accent cursor-pointer rounded p-1.5 disabled:opacity-30">
              <ChevronLeft className="size-4" />
            </button>
            <button aria-label="下一页" disabled={pageIdx >= pages.length - 1} onClick={() => setPageIdx((i) => i + 1)}
              className="hover:bg-accent cursor-pointer rounded p-1.5 disabled:opacity-30">
              <ChevronRight className="size-4" />
            </button>
          </div>
        )}
        <button aria-label="关闭原文" onClick={onClose} className="hover:bg-accent cursor-pointer rounded p-1.5">
          <X className="size-4" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {!hl ? (
          <p className="text-muted-foreground text-sm">定位原文…</p>
        ) : hl.kind === "pdf" ? (
          <PdfPageView docId={hl.doc_id} page={pages[pageIdx]} boxes={hl.boxes} />
        ) : (
          <HtmlDocView docId={hl.doc_id} seqs={hl.block_seqs} />
        )}
      </div>
    </aside>
  );
}

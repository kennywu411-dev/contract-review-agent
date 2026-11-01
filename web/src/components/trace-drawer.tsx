"use client";

import { X } from "lucide-react";
import { useEffect, useState } from "react";

import { TraceSteps } from "@/components/trace-steps";
import { fmtMs, fmtUsd, getJSON, type Trace } from "@/lib/api";

export function TraceDrawer({ run, traceId, onClose }: { run: string; traceId: string; onClose: () => void }) {
  const [t, setT] = useState<Trace | null>(null);
  useEffect(() => {
    setT(null);
    getJSON<Trace>(`/api/runs/${encodeURIComponent(run)}/traces/${encodeURIComponent(traceId)}`).then(setT).catch(() => setT(null));
  }, [run, traceId]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const result = t?.result as { kind?: string; sentences?: { text: string; citations: string[] }[]; refuse_reason?: string } | undefined;
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/40" onClick={onClose}>
      <aside
        role="dialog"
        aria-label="trace 回放"
        onClick={(e) => e.stopPropagation()}
        className="bg-background flex h-full w-full max-w-2xl flex-col border-l shadow-xl"
      >
        <div className="flex items-start gap-2 border-b p-4">
          <div className="min-w-0 flex-1">
            <p className="text-muted-foreground font-mono text-xs">trace {traceId}</p>
            <p className="font-medium">{t?.question ?? "加载中…"}</p>
            {t && (
              <p className="text-muted-foreground mt-1 text-xs">
                {t.tenant} · {t.model} · 结束：{t.end} · {fmtMs(t.latency_ms)} · {fmtUsd(t.cost_usd)}
              </p>
            )}
          </div>
          <button aria-label="关闭" onClick={onClose} className="hover:bg-accent cursor-pointer rounded p-1.5">
            <X className="size-4" />
          </button>
        </div>
        {t && (
          <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 text-sm">
            <section className="space-y-1.5">
              <h3 className="text-xs font-medium">原始回答（{result?.kind}）</h3>
              {result?.refuse_reason && <p>{result.refuse_reason}</p>}
              <ul className="list-disc space-y-1 pl-5">
                {result?.sentences?.map((s, i) => (
                  <li key={i}>
                    {s.text} <span className="text-primary font-mono text-xs">{s.citations.join(" ")}</span>
                  </li>
                ))}
              </ul>
            </section>
            {t.verify && (
              <section className="space-y-1.5">
                <h3 className="text-xs font-medium">
                  校验后（{t.verify.kind}）· 删 {t.verify.dropped.length} 句 · 改正引用 {t.verify.repaired} 处 · {fmtMs(t.verify.latency_ms)}
                </h3>
                {t.verify.dropped.map((d, i) => (
                  <p key={i} className="text-muted-foreground text-xs">
                    <span className="line-through">{d.sentence.text}</span> <span className="text-destructive">（{d.reason}）</span>
                  </p>
                ))}
              </section>
            )}
            {t.judge && (
              <section className="space-y-1">
                <h3 className="text-xs font-medium">Sonnet 评判</h3>
                <pre className="bg-muted overflow-x-auto rounded p-2 font-mono text-xs whitespace-pre-wrap">
                  {JSON.stringify(t.judge, null, 2)}
                </pre>
              </section>
            )}
            <section className="space-y-2">
              <h3 className="text-xs font-medium">agent 步骤</h3>
              <TraceSteps steps={t.steps} />
            </section>
          </div>
        )}
      </aside>
    </div>
  );
}

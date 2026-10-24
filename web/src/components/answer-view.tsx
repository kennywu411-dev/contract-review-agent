"use client";

import { AlertTriangle, ChevronRight, ShieldCheck } from "lucide-react";
import { useState } from "react";

import type { Citation, Sentence, Verified } from "@/lib/api";
import { cn } from "@/lib/utils";

const KIND = {
  answer: { label: "已校验", cls: "bg-primary/15 text-primary" },
  partial: { label: "部分回答", cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
  refuse: { label: "拒答", cls: "bg-muted text-muted-foreground" },
  clarify: { label: "需要补充信息", cls: "bg-sky-500/15 text-sky-600 dark:text-sky-400" },
} as const;

export function CiteChip({ id, citation, active, onOpen }: { id: string; citation?: Citation; active: boolean; onOpen: (id: string) => void }) {
  if (!citation) return null;
  const title =
    citation.kind === "doc"
      ? `《${citation.title}》${citation.pages.length ? ` 第${citation.pages.join(",")}页` : ` ${citation.articles.join("、")}`}`
      : `查表：${citation.sql}`;
  return (
    <button
      onClick={() => onOpen(id)}
      title={title}
      aria-label={`查看出处 ${id}：${title}`}
      className={cn(
        "mx-0.5 inline-flex cursor-pointer items-center rounded px-1 align-baseline font-mono text-[11px] leading-4 transition-colors",
        citation.kind === "doc" ? "bg-primary/15 text-primary hover:bg-primary/25" : "bg-sky-500/15 text-sky-600 hover:bg-sky-500/25 dark:text-sky-400",
        active && "ring-primary ring-1",
      )}
    >
      {id}
    </button>
  );
}

export function AnswerView({
  verified,
  citations,
  activeId,
  onOpen,
}: {
  verified: Verified;
  citations: Record<string, Citation>;
  activeId: string | null;
  onOpen: (id: string) => void;
}) {
  const [showDropped, setShowDropped] = useState(false);
  const kind = KIND[verified.kind] ?? KIND.answer;
  const line = (s: Sentence, i: number) => (
    <li key={i} className={cn(s.kind === "note" && "text-muted-foreground")}>
      {s.text}
      {s.citations.map((c) => (
        <CiteChip key={c} id={c} citation={citations[c]} active={activeId === c} onOpen={onOpen} />
      ))}
    </li>
  );
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium", kind.cls)}>
          <ShieldCheck className="size-3.5" />
          {kind.label}
        </span>
        {verified.repaired > 0 && <span className="text-muted-foreground">改正了 {verified.repaired} 处引用</span>}
        {verified.verifier_error && (
          <span className="text-destructive inline-flex items-center gap-1">
            <AlertTriangle className="size-3.5" />
            校验器出错，已按拒答处理
          </span>
        )}
      </div>
      {verified.kind === "refuse" && <p className="text-sm">{verified.refuse_reason}</p>}
      {verified.kind === "clarify" && <p className="text-sm">{verified.clarification}</p>}
      {verified.kind === "partial" && verified.unanswered && (
        <p className="rounded-md border border-dashed px-3 py-2 text-sm">
          <span className="text-muted-foreground">无法回答的部分：</span>
          {verified.unanswered}
        </p>
      )}
      {verified.kept.length > 0 && <ul className="list-disc space-y-1.5 pl-5 text-sm leading-relaxed">{verified.kept.map(line)}</ul>}
      {verified.dropped.length > 0 && (
        <div className="text-xs">
          <button
            onClick={() => setShowDropped((o) => !o)}
            aria-expanded={showDropped}
            className="text-muted-foreground hover:text-foreground inline-flex cursor-pointer items-center gap-1"
          >
            <ChevronRight className={cn("size-3.5 transition-transform", showDropped && "rotate-90")} />
            校验删除了 {verified.dropped.length} 句
          </button>
          {showDropped && (
            <ul className="mt-1.5 space-y-1 pl-5">
              {verified.dropped.map((d, i) => (
                <li key={i} className="text-muted-foreground">
                  <span className="line-through">{d.sentence.text}</span>
                  <span className="text-destructive ml-1">（{d.reason}）</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

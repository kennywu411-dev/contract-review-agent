"use client";

import { ChevronRight, Database, FileSearch, HelpCircle, MessageSquareText } from "lucide-react";
import { useState } from "react";

import { fmtMs, fmtUsd, type Step } from "@/lib/api";
import { cn } from "@/lib/utils";

const TOOL = {
  search_docs: { label: "检索文档", icon: FileSearch },
  run_sql: { label: "查表（DataPilot MCP）", icon: Database },
  ask_clarification: { label: "反问", icon: HelpCircle },
  final_answer: { label: "作答", icon: MessageSquareText },
} as const;

function ToolCallView({ call }: { call: Step["tool_calls"][number] }) {
  const [open, setOpen] = useState(false);
  const meta = TOOL[call.name as keyof typeof TOOL] ?? { label: call.name, icon: FileSearch };
  const Icon = meta.icon;
  const arg = (call.input.query ?? call.input.sql ?? call.input.question ?? "") as string;
  return (
    <div className="rounded-md border">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="hover:bg-accent/50 flex w-full cursor-pointer items-center gap-2 px-2.5 py-1.5 text-left text-xs transition-colors"
      >
        <ChevronRight className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-90")} />
        <Icon className="text-primary size-3.5 shrink-0" />
        <span className="shrink-0 font-medium">{meta.label}</span>
        <span className="text-muted-foreground truncate font-mono">{arg}</span>
        {call.is_error && <span className="text-destructive ml-auto shrink-0">出错</span>}
        {call.ms !== undefined && <span className="text-muted-foreground ml-auto shrink-0">{fmtMs(call.ms)}</span>}
      </button>
      {open && (
        <div className="space-y-2 border-t p-2.5 text-xs">
          <pre className="bg-muted overflow-x-auto rounded p-2 font-mono whitespace-pre-wrap">
            {JSON.stringify(call.input, null, 2)}
          </pre>
          {call.output && (
            <pre className="bg-muted max-h-72 overflow-auto rounded p-2 font-mono whitespace-pre-wrap">{call.output}</pre>
          )}
        </div>
      )}
    </div>
  );
}

export function TraceSteps({ steps }: { steps: Step[] }) {
  return (
    <ol className="space-y-2">
      {steps.map((s) => {
        const u = s.usage ?? {};
        return (
          <li key={s.step} className="space-y-1.5">
            <div className="text-muted-foreground flex flex-wrap gap-x-3 text-xs">
              <span className="text-foreground font-medium">第 {s.step} 步</span>
              <span>模型 {fmtMs(s.llm_ms)}</span>
              <span>
                输入 {u.input_tokens ?? 0} · 缓存读 {u.cache_read_input_tokens ?? 0} · 缓存写{" "}
                {u.cache_creation_input_tokens ?? 0} · 输出 {u.output_tokens ?? 0}
              </span>
              <span>{fmtUsd(s.cost_usd)}</span>
            </div>
            <div className="space-y-1.5 pl-2">
              {s.tool_calls.map((c, i) => (
                <ToolCallView key={i} call={c} />
              ))}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

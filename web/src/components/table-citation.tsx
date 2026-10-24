"use client";

import { X } from "lucide-react";

import type { Citation } from "@/lib/api";

export function TableCitationView({ id, c, onClose }: { id: string; c: Extract<Citation, { kind: "table" }>; onClose: () => void }) {
  return (
    <aside className="bg-card flex h-full min-h-0 flex-col border-l" aria-label="查表结果">
      <div className="flex items-start gap-2 border-b p-3">
        <div className="min-w-0 flex-1">
          <p className="text-muted-foreground text-xs">[{id}] 查表结果（DataPilot MCP，只读）</p>
          <pre className="mt-1 overflow-x-auto font-mono text-xs whitespace-pre-wrap">{c.sql}</pre>
        </div>
        <button aria-label="关闭" onClick={onClose} className="hover:bg-accent cursor-pointer rounded p-1.5">
          <X className="size-4" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-3">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted-foreground border-b text-left">
              {c.columns.map((col) => (
                <th key={col} className="px-2 py-1.5 font-medium whitespace-nowrap">{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {c.rows.map((r, i) => (
              <tr key={i} className="border-b last:border-0">
                {r.map((v, j) => (
                  <td key={j} className="px-2 py-1.5 align-top">{String(v ?? "")}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </aside>
  );
}

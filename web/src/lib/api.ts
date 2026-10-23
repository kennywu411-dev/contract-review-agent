// 后端接口与 SSE 客户端。EventSource 只支持 GET，这里用 fetch + ReadableStream 解析 SSE（同 DataPilot）。

// docpilot serve 时前后端同源，用相对路径；开发时 `npm run dev` 设 NEXT_PUBLIC_API_BASE 指向 8000 端口
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";
const IDLE_TIMEOUT_MS = 60_000; // 后端每 15 秒发心跳，60 秒没有任何数据视为断线

export type School = { id: string; name: string };

export type DocCitation = {
  kind: "doc";
  chunk_id: string;
  doc_id: string;
  school: string;
  title: string;
  section_path: string[];
  pages: number[];
  articles: string[];
  text: string;
  score: number;
};
export type TableCitation = { kind: "table"; sql: string; columns: string[]; rows: unknown[][] };
export type Citation = DocCitation | TableCitation;

export type Sentence = { kind: "fact" | "note"; text: string; citations: string[]; index?: number };

export type ToolCall = {
  name: string;
  input: Record<string, unknown>;
  output?: string;
  is_error?: boolean;
  citations?: string[];
  ms?: number;
};
export type Step = {
  step: number;
  stop_reason: string;
  usage: Record<string, number>;
  cost_usd: number;
  llm_ms: number;
  assistant: { type: string; text?: string; name?: string; input?: unknown; chars?: number }[];
  tool_calls: ToolCall[];
};

export type AgentAnswer = {
  question: string;
  school: string;
  kind: string;
  sentences: Sentence[];
  refuse_reason: string;
  unanswered: string;
  clarification: string;
  invalid_citations: string[];
  citations: Record<string, Citation>;
  steps: number;
  cost_usd: number;
  latency_ms: number;
  trace: { trace_id: string };
};

export type Verified = {
  kind: "answer" | "partial" | "refuse" | "clarify";
  kept: Sentence[];
  dropped: { sentence: Sentence; reason: string }[];
  refuse_reason: string;
  unanswered: string;
  clarification: string;
  verifier_error: string;
  repaired: number;
  cost_usd: number;
  latency_ms: number;
};

export type AskEvent =
  | { event: "step"; data: Step }
  | { event: "answer"; data: AgentAnswer }
  | { event: "verified"; data: Verified }
  | { event: "error"; data: { message: string } }
  | { event: "done"; data: Record<string, never> };

export type Highlight = {
  doc_id: string;
  kind: "pdf" | "html" | "docx";
  pages: number[];
  block_seqs: number[];
  articles: string[];
  boxes: { seq: number; page: number; bbox: [number, number, number, number]; page_width: number; page_height: number }[];
};

export type HtmlDoc = {
  doc_id: string;
  title: string;
  url: string;
  blocks: { seq: number; kind: string; level: number | null; text: string }[];
};

export type RunInfo = { run: string; kind: "retrieval" | "agent" | "d4"; n: number | null };
export type RunDetail = {
  run: string;
  kind: RunInfo["kind"];
  summary: Record<string, unknown>;
  rows: Record<string, unknown>[];
};
export type StageRow = {
  trace_id: string;
  question: string;
  school: string;
  steps: number;
  llm_ms: number;
  search_ms: number;
  sql_ms: number;
  verify_ms: number;
  latency_ms: number;
  cost_usd: number;
  cache_read: number;
  cache_write: number;
  input: number;
  output: number;
};
export type Trace = {
  trace_id: string;
  model: string;
  question: string;
  school: string;
  steps: Step[];
  end: string;
  result: Record<string, unknown>;
  citations: Record<string, Citation>;
  cost_usd: number;
  latency_ms: number;
  verify?: Verified;
  judge?: Record<string, unknown>;
};

export async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

export const pdfUrl = (docId: string) => `${API_BASE}/api/docs/${encodeURIComponent(docId)}/pdf`;

/** 提交问题并逐个回调 SSE 事件；返回取消函数。 */
export function ask(
  question: string,
  school: string,
  onEvent: (e: AskEvent) => void,
  sessionId?: string,
): () => void {
  const ctrl = new AbortController();
  (async () => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const arm = () => {
      clearTimeout(timer);
      timer = setTimeout(() => ctrl.abort(), IDLE_TIMEOUT_MS);
    };
    try {
      arm();
      const r = await fetch(`${API_BASE}/api/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, school, session_id: sessionId }),
        signal: ctrl.signal,
      });
      if (!r.ok || !r.body) throw new Error(`${r.status} ${await r.text()}`);
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        arm();
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.search(/\r?\n\r?\n/)) >= 0) {
          const raw = buf.slice(0, idx);
          buf = buf.slice(idx).replace(/^\r?\n\r?\n/, "");
          let name = "message";
          const data: string[] = [];
          for (const line of raw.split(/\r?\n/)) {
            if (line.startsWith("event:")) name = line.slice(6).trim();
            else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
          }
          if (!data.length) continue; // 心跳（注释行）
          onEvent({ event: name, data: JSON.parse(data.join("\n")) } as AskEvent);
        }
      }
    } catch (e) {
      if (!ctrl.signal.aborted || timer) {
        onEvent({ event: "error", data: { message: e instanceof Error ? e.message : String(e) } });
        onEvent({ event: "done", data: {} });
      }
    } finally {
      clearTimeout(timer);
    }
  })();
  return () => ctrl.abort();
}

export const fmtMs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`);
export const fmtUsd = (x: number) => `$${x.toFixed(x < 0.01 ? 5 : 3)}`;
export const pct = (x: unknown) => (typeof x === "number" ? `${(x * 100).toFixed(1)}%` : "-");

// 后端接口与 SSE 客户端。EventSource 只支持 GET，这里用 fetch + ReadableStream 解析 SSE（同 DataPilot）。

// cra serve 时前后端同源，用相对路径；开发时 `npm run dev` 设 NEXT_PUBLIC_API_BASE 指向 8000 端口
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";
const IDLE_TIMEOUT_MS = 60_000; // 后端每 15 秒发心跳，60 秒没有任何数据视为断线

export type Tenant = { id: string; name: string };

export type DocCitation = {
  kind: "doc";
  chunk_id: string;
  doc_id: string;
  tenant: string;
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
  tenant: string;
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

export type RunInfo = {
  run: string;
  kind: "retrieval" | "agent" | "d4" | "dialog" | "review" | "review_d4" | "review_dialog" | "review_longdialog" | "contract_retrieval";
  n: number | null;
};
export type RunDetail = {
  run: string;
  kind: RunInfo["kind"];
  summary: Record<string, unknown>;
  rows: Record<string, unknown>[];
};
export type StageRow = {
  trace_id: string;
  question: string;
  tenant: string;
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
  tenant: string;
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

// ---------------- 合同审查 ----------------

export type Finding = {
  clause: string | null;
  clause_raw?: string;
  quote: string;
  quote_found: boolean;
  risk_type: string;
  level: "高" | "中" | "低";
  reason: string;
  suggestion: string;
  citations: string[];
  needs_human_review: boolean;
  verify_flags?: string[];
  revised_from?: string;
};

export type ReviewResult = {
  tenant: string;
  kind: "review" | "clarify" | "followup" | "fallback";
  our_side: string;
  summary: string;
  findings: Finding[];
  clarification: string;
  answer: string;
  answer_clause: string | null;
  answer_citations: string[];
  answer_needs_human: boolean;
  invalid_citations: string[];
  citations: Record<string, Citation>;
  steps: number;
  cost_usd: number;
  latency_ms: number;
  trace: { trace_id: string };
};

export type VerifiedReview = {
  findings: Finding[];
  flagged: number;
  revised: number;
  repaired: number;
  verifier_error: string;
  cost_usd: number;
  latency_ms: number;
};

export type ReviewEvent =
  | { event: "session"; data: { session_id: string } }
  | { event: "step"; data: Step }
  | { event: "review"; data: ReviewResult }
  | { event: "verified"; data: VerifiedReview }
  | { event: "error"; data: { message: string } }
  | { event: "done"; data: Record<string, never> };

export type Sample = {
  id: string;
  title: string;
  tenant: string;
  our_name: string;
  counterparty: string;
  text: string;
  constructed: boolean;
};

export const RISK_TYPES: Record<string, string> = {
  penalty: "违约金过高或只约束一方",
  liability: "免责或责任无上限",
  termination: "单方解除权",
  inspection: "检验期过短或视为合格",
  payment: "付款周期过长或背靠背",
  confidentiality: "保密缺失或不对等",
  ip: "知识产权归属不利",
  jurisdiction: "管辖不利或或裁或诉",
  auto_renew: "自动续约",
  deposit: "定金超过 20%",
  unit_price: "单价高于台账历史价",
  other: "其他",
};

/** 合同审查或追问：首轮传 contract，追问传 question（同一 sessionId）。 */
export function review(
  body: { contract?: string; question?: string; tenant: string; session_id?: string },
  onEvent: (e: ReviewEvent) => void,
): () => void {
  return streamPost("/api/review", body, onEvent as (e: { event: string; data: unknown }) => void);
}

/** 提交问题并逐个回调 SSE 事件；返回取消函数。 */
export function ask(
  question: string,
  tenant: string,
  onEvent: (e: AskEvent) => void,
  sessionId?: string,
): () => void {
  return streamPost(
    "/api/ask",
    { question, tenant, session_id: sessionId },
    onEvent as (e: { event: string; data: unknown }) => void,
  );
}

function streamPost(
  path: string,
  body: unknown,
  onEvent: (e: { event: string; data: unknown }) => void,
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
      const r = await fetch(`${API_BASE}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
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
          onEvent({ event: name, data: JSON.parse(data.join("\n")) });
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

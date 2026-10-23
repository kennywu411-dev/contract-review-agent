"use client";

import { ChevronRight, Loader2, MessageSquarePlus, SendHorizonal } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { AnswerView } from "@/components/answer-view";
import { TableCitationView } from "@/components/table-citation";
import { SourceViewer } from "@/components/source-viewer";
import { TopNav } from "@/components/top-nav";
import { TraceSteps } from "@/components/trace-steps";
import { type AgentAnswer, ask, fmtMs, fmtUsd, getJSON, type Tenant, type Step, type Verified } from "@/lib/api";
import { cn } from "@/lib/utils";

type Turn = {
  id: number;
  question: string;
  tenant: string;
  steps: Step[];
  answer?: AgentAnswer;
  verified?: Verified;
  error?: string;
  done: boolean;
};

const EXAMPLES = ["约定的违约金过高，可以请求法院调低吗？", "定金最多能约定合同金额的多少？", "我们公司作为买方，付款期限最长可以约定多少天？"];

function TurnView({ turn, active, onOpen }: { turn: Turn; active: string | null; onOpen: (turnId: number, cid: string) => void }) {
  const [showTrace, setShowTrace] = useState(false);
  const pending = !turn.done;
  return (
    <section className="space-y-3">
      <div className="flex justify-end">
        <p className="bg-primary/10 max-w-[85%] rounded-2xl rounded-br-sm px-4 py-2 text-sm">{turn.question}</p>
      </div>
      <div className="bg-card space-y-3 rounded-xl border p-4">
        {turn.error && <p className="text-destructive text-sm">出错了：{turn.error}</p>}
        {pending && (
          <p className="text-muted-foreground inline-flex items-center gap-2 text-sm">
            <Loader2 className="size-4 animate-spin" />
            {turn.answer ? "正在逐句核对引用…" : turn.steps.length ? `第 ${turn.steps.length} 步完成，继续思考…` : "检索中…"}
          </p>
        )}
        {turn.verified && turn.answer && (
          <AnswerView
            verified={turn.verified}
            citations={turn.answer.citations}
            activeId={active}
            onOpen={(cid) => onOpen(turn.id, cid)}
          />
        )}
        {(turn.steps.length > 0 || turn.answer) && (
          <div className="border-t pt-2">
            <button
              onClick={() => setShowTrace((o) => !o)}
              aria-expanded={showTrace}
              className="text-muted-foreground hover:text-foreground inline-flex cursor-pointer items-center gap-1 text-xs"
            >
              <ChevronRight className={cn("size-3.5 transition-transform", showTrace && "rotate-90")} />
              agent 过程（{turn.steps.length} 步）
              {turn.answer && turn.verified && (
                <span className="ml-2">
                  共 {fmtMs(turn.answer.latency_ms + turn.verified.latency_ms)} · {fmtUsd(turn.answer.cost_usd + turn.verified.cost_usd)}
                </span>
              )}
            </button>
            {showTrace && (
              <div className="mt-2">
                <TraceSteps steps={turn.steps} />
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

export default function LawAskPage() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenant, setTenant] = useState("huaxin");
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [open, setOpen] = useState<{ turnId: number; cid: string } | null>(null);
  // 同一会话内的追问共享记忆（后端按 session_id 保存最近几轮和摘要）；新对话或换公司时重置
  const [sessionId, setSessionId] = useState(() => Math.random().toString(36).slice(2, 12));
  const newChat = () => {
    setTurns([]);
    setOpen(null);
    setSessionId(Math.random().toString(36).slice(2, 12));
  };
  const bottom = useRef<HTMLDivElement>(null);
  const busy = turns.some((t) => !t.done);

  useEffect(() => {
    getJSON<Tenant[]>("/api/tenants").then(setTenants).catch(() => setTenants([]));
    try {
      const s = localStorage.getItem("cra-tenant");
      if (s) setTenant(s);
    } catch {}
  }, []);
  useEffect(() => {
    // 新版 Chromium 的 scrollIntoView 返回 Promise，不能作为 effect 的返回值（会被当成清理函数调用）
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const submit = (q: string) => {
    const question = q.trim();
    if (!question || busy) return;
    const id = Date.now();
    setTurns((ts) => [...ts, { id, question, tenant, steps: [], done: false }]);
    setInput("");
    const patch = (f: (t: Turn) => Turn) => setTurns((ts) => ts.map((t) => (t.id === id ? f(t) : t)));
    ask(question, tenant, (e) => {
      if (e.event === "step") patch((t) => ({ ...t, steps: [...t.steps, e.data] }));
      else if (e.event === "answer") patch((t) => ({ ...t, answer: e.data }));
      else if (e.event === "verified") patch((t) => ({ ...t, verified: e.data }));
      else if (e.event === "error") patch((t) => ({ ...t, error: e.data.message }));
      else if (e.event === "done") patch((t) => ({ ...t, done: true }));
    }, sessionId);
  };

  const openTurn = turns.find((t) => t.id === open?.turnId);
  const citation = open && openTurn?.answer?.citations[open.cid];
  const tenantName = tenants.find((s) => s.id === tenant)?.name ?? tenant;

  return (
    <div className="flex h-dvh flex-col">
      <TopNav />
      <div className={cn("grid min-h-0 flex-1", citation ? "lg:grid-cols-[minmax(0,1fr)_minmax(420px,46%)]" : "grid-cols-1")}>
        <main className="flex min-h-0 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
              {turns.length === 0 && (
                <div className="space-y-4 py-10">
                  <h1 className="text-2xl font-semibold tracking-tight">法规与公司规范问答</h1>
                  <p className="text-muted-foreground text-sm leading-relaxed">
                    答案来自《民法典》、合同编通则解释、民事诉讼法、官方合同示范文本和{tenantName}的合同审查规范，逐句标出处，点击编号看原文；查不到就明确拒答。涉及供应商历史价格时，agent 会通过 DataPilot 只读查询台账。
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {EXAMPLES.map((q) => (
                      <button key={q} onClick={() => submit(q)}
                        className="hover:bg-accent cursor-pointer rounded-full border px-3 py-1.5 text-sm transition-colors">
                        {q}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {turns.map((t) => (
                <TurnView key={t.id} turn={t} active={open?.turnId === t.id ? open.cid : null}
                  onOpen={(turnId, cid) => setOpen({ turnId, cid })} />
              ))}
              <div ref={bottom} />
            </div>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              submit(input);
            }}
            className="border-t p-3"
          >
            <div className="mx-auto flex max-w-3xl items-end gap-2">
              {turns.length > 0 && (
                <button
                  type="button"
                  onClick={newChat}
                  disabled={busy}
                  aria-label="新对话"
                  title="新对话（清空上下文）"
                  className="hover:bg-accent inline-flex h-10 w-10 cursor-pointer items-center justify-center rounded-md border disabled:opacity-40"
                >
                  <MessageSquarePlus className="size-4" />
                </button>
              )}
              <label className="sr-only" htmlFor="tenant">公司</label>
              <select
                id="tenant"
                value={tenant}
                onChange={(e) => {
                  setTenant(e.target.value);
                  newChat();
                  try {
                    localStorage.setItem("cra-tenant", e.target.value);
                  } catch {}
                }}
                className="bg-background h-10 cursor-pointer rounded-md border px-2 text-sm"
              >
                {tenants.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
              <label className="sr-only" htmlFor="q">问题</label>
              <textarea
                id="q"
                value={input}
                maxLength={2000}
                rows={1}
                placeholder={turns.length ? "可以接着追问，例如：那定金和违约金能同时主张吗？" : "例如：违约金过高怎么办？（Enter 发送，Shift+Enter 换行）"}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    submit(input);
                  }
                }}
                className="bg-background focus-visible:ring-ring min-h-10 flex-1 resize-none rounded-md border px-3 py-2 text-sm outline-none focus-visible:ring-2"
              />
              <button
                type="submit"
                disabled={busy || !input.trim()}
                aria-label="发送"
                className="bg-primary text-primary-foreground inline-flex h-10 w-10 cursor-pointer items-center justify-center rounded-md transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy ? <Loader2 className="size-4 animate-spin" /> : <SendHorizonal className="size-4" />}
              </button>
            </div>
          </form>
        </main>
        {citation && open &&
          (citation.kind === "doc" ? (
            <SourceViewer id={open.cid} citation={citation} onClose={() => setOpen(null)} />
          ) : (
            <TableCitationView id={open.cid} c={citation} onClose={() => setOpen(null)} />
          ))}
      </div>
    </div>
  );
}

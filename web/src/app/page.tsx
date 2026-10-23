"use client";

import { ChevronRight, Loader2, SendHorizonal, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { AnswerView } from "@/components/answer-view";
import { SourceViewer } from "@/components/source-viewer";
import { TopNav } from "@/components/top-nav";
import { TraceSteps } from "@/components/trace-steps";
import { type AgentAnswer, ask, type Citation, fmtMs, fmtUsd, getJSON, type School, type Step, type Verified } from "@/lib/api";
import { cn } from "@/lib/utils";

type Turn = {
  id: number;
  question: string;
  school: string;
  steps: Step[];
  answer?: AgentAnswer;
  verified?: Verified;
  error?: string;
  done: boolean;
};

const EXAMPLES: Record<string, string[]> = {
  bnu: ["缓考怎么申请？", "京师奖学金一等多少钱？", "转系难不难？"],
  upc: ["国家奖学金一年能拿多少钱？", "挂了一门课能补考吗？", "转专业需要什么条件？"],
  njust: ["转专业一年有几次机会？", "本科一等奖学金多少钱？"],
  lzu: ["大二还能转专业吗？", "休学最长能休多久？"],
  cumt: ["推免需要满足哪些条件？", "学士学位授予要求是什么？"],
  zzu: ["绩点多少才能申请免修？", "挂科多了会被退学吗？"],
};

function TableCitationView({ id, c, onClose }: { id: string; c: Extract<Citation, { kind: "table" }>; onClose: () => void }) {
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

export default function AskPage() {
  const [schools, setSchools] = useState<School[]>([]);
  const [school, setSchool] = useState("bnu");
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [open, setOpen] = useState<{ turnId: number; cid: string } | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const busy = turns.some((t) => !t.done);

  useEffect(() => {
    getJSON<School[]>("/api/schools").then(setSchools).catch(() => setSchools([]));
    try {
      const s = localStorage.getItem("dp-school");
      if (s) setSchool(s);
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
    setTurns((ts) => [...ts, { id, question, school, steps: [], done: false }]);
    setInput("");
    const patch = (f: (t: Turn) => Turn) => setTurns((ts) => ts.map((t) => (t.id === id ? f(t) : t)));
    ask(question, school, (e) => {
      if (e.event === "step") patch((t) => ({ ...t, steps: [...t.steps, e.data] }));
      else if (e.event === "answer") patch((t) => ({ ...t, answer: e.data }));
      else if (e.event === "verified") patch((t) => ({ ...t, verified: e.data }));
      else if (e.event === "error") patch((t) => ({ ...t, error: e.data.message }));
      else if (e.event === "done") patch((t) => ({ ...t, done: true }));
    });
  };

  const openTurn = turns.find((t) => t.id === open?.turnId);
  const citation = open && openTurn?.answer?.citations[open.cid];
  const schoolName = schools.find((s) => s.id === school)?.name ?? school;

  return (
    <div className="flex h-dvh flex-col">
      <TopNav />
      <div className={cn("grid min-h-0 flex-1", citation ? "lg:grid-cols-[minmax(0,1fr)_minmax(420px,46%)]" : "grid-cols-1")}>
        <main className="flex min-h-0 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
              {turns.length === 0 && (
                <div className="space-y-4 py-10">
                  <h1 className="text-2xl font-semibold tracking-tight">问{schoolName}的规章制度</h1>
                  <p className="text-muted-foreground text-sm leading-relaxed">
                    答案来自本校和国家层面的正式文件，逐句标出处，点击编号看原文高亮；文件里查不到就明确拒答。需要查金额、比例时，agent 会通过 DataPilot 查表。
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {(EXAMPLES[school] ?? []).map((q) => (
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
              <label className="sr-only" htmlFor="school">学校</label>
              <select
                id="school"
                value={school}
                onChange={(e) => {
                  setSchool(e.target.value);
                  try {
                    localStorage.setItem("dp-school", e.target.value);
                  } catch {}
                }}
                className="bg-background h-10 cursor-pointer rounded-md border px-2 text-sm"
              >
                {schools.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
              <label className="sr-only" htmlFor="q">问题</label>
              <textarea
                id="q"
                value={input}
                maxLength={2000}
                rows={1}
                placeholder="例如：缓考怎么申请？（Enter 发送，Shift+Enter 换行）"
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

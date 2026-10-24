"use client";

import { AlertTriangle, Check, ChevronRight, Copy, FileText, Loader2, Pencil, SendHorizonal, ShieldCheck, UserCheck } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { CiteChip } from "@/components/answer-view";
import { SourceViewer } from "@/components/source-viewer";
import { TableCitationView } from "@/components/table-citation";
import { TopNav } from "@/components/top-nav";
import { TraceSteps } from "@/components/trace-steps";
import {
  type Finding,
  fmtMs,
  fmtUsd,
  getJSON,
  review,
  type ReviewResult,
  RISK_TYPES,
  type Sample,
  type Step,
  type Tenant,
  type VerifiedReview,
} from "@/lib/api";
import { splitClauses } from "@/lib/clauses.mjs";
import { cn } from "@/lib/utils";

type Turn = {
  id: number;
  question: string; // 首轮为空
  steps: Step[];
  result?: ReviewResult;
  verified?: VerifiedReview;
  error?: string;
  done: boolean;
};

const LEVEL = {
  高: { badge: "bg-destructive/15 text-destructive", mark: "bg-destructive/10 border-destructive" },
  中: { badge: "bg-amber-500/15 text-amber-700 dark:text-amber-400", mark: "bg-amber-500/10 border-amber-500" },
  低: { badge: "bg-muted text-muted-foreground", mark: "bg-muted/60 border-muted-foreground/40" },
} as const;

const FLAG_TEXT: Record<string, string> = {
  unsupported: "依据未能在检索原文中核实",
  partial: "依据只能部分核实",
  unverified: "校验器出错，未能核实",
  quote_mismatch: "摘录与合同原文不一致",
};
const flagText = (f: string) =>
  f.startsWith("unretrieved:") ? `点名的条文未被检索到（${f.slice(12)}）` : (FLAG_TEXT[f] ?? f);

function ContractView({ text, findings, active, onPick }: { text: string; findings: Finding[]; active: string | null; onPick: (no: string) => void }) {
  const byClause = useMemo(() => {
    const m = new Map<string, Finding[]>();
    for (const f of findings) if (f.clause) m.set(f.clause, [...(m.get(f.clause) ?? []), f]);
    return m;
  }, [findings]);
  const rank = { 高: 0, 中: 1, 低: 2 } as const;
  return (
    <div className="space-y-2 font-[system-ui] text-sm leading-7">
      {splitClauses(text).map((c, i) => {
        const fs = c.no ? byClause.get(c.no) : undefined;
        const top = fs?.slice().sort((a, b) => rank[a.level] - rank[b.level])[0];
        return (
          <div
            key={i}
            id={c.no ? `clause-${c.no}` : undefined}
            onClick={() => c.no && fs && onPick(c.no)}
            className={cn(
              "rounded-md px-3 py-1.5 transition-colors",
              top && cn("cursor-pointer border-l-4", LEVEL[top.level].mark),
              c.no && c.no === active && "ring-primary ring-2",
            )}
          >
            {c.lines.map((l, j) => (
              <p key={j} className={cn(j === 0 && c.no && "font-medium")}>{l}</p>
            ))}
            {fs && (
              <p className="text-muted-foreground mt-1 text-xs">
                {fs.map((f) => `${RISK_TYPES[f.risk_type] ?? f.risk_type}（${f.level}）`).join("；")}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [ok, setOk] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(text).then(() => {
          setOk(true);
          setTimeout(() => setOk(false), 1500);
        });
      }}
      aria-label="复制修改建议"
      className="text-muted-foreground hover:text-foreground inline-flex cursor-pointer items-center gap-1 text-xs"
    >
      {ok ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      {ok ? "已复制" : "复制"}
    </button>
  );
}

function FindingCard({
  f, i, result, active, onCite, onClause,
}: {
  f: Finding; i: number; result: ReviewResult; active: string | null;
  onCite: (cid: string) => void; onClause: (no: string) => void;
}) {
  return (
    <article className="bg-card space-y-2 rounded-xl border p-4" aria-label={`意见 ${i + 1}`}>
      <header className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground font-mono text-xs">#{i + 1}</span>
        <button onClick={() => f.clause && onClause(f.clause)} disabled={!f.clause}
          className="cursor-pointer font-medium underline-offset-4 hover:underline disabled:cursor-default disabled:no-underline">
          {f.clause ?? "缺失条款"}
        </button>
        <span className="bg-secondary rounded px-1.5 py-0.5 text-xs">{RISK_TYPES[f.risk_type] ?? f.risk_type}</span>
        <span className={cn("rounded px-1.5 py-0.5 text-xs", LEVEL[f.level].badge)}>{f.level}风险</span>
        {f.needs_human_review && (
          <span className="inline-flex items-center gap-1 rounded bg-sky-500/15 px-1.5 py-0.5 text-xs text-sky-700 dark:text-sky-400">
            <UserCheck className="size-3" />转法务复核
          </span>
        )}
      </header>
      {f.quote && <blockquote className="text-muted-foreground border-l-2 pl-3 text-sm">“{f.quote}”</blockquote>}
      <p className="text-sm leading-relaxed">
        {f.reason}
        {f.citations.map((c) => (
          <CiteChip key={c} id={c} citation={result.citations[c]} active={active === c} onOpen={onCite} />
        ))}
      </p>
      {f.verify_flags?.length ? (
        <p className="inline-flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          校验：{f.verify_flags.map(flagText).join("；")}，已转人工复核
        </p>
      ) : null}
      {f.revised_from && <p className="text-muted-foreground text-xs">理由已由校验器改写为只保留有原文依据的部分。</p>}
      <div className="bg-muted/50 rounded-lg p-3">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-muted-foreground text-xs">修改建议</span>
          <CopyButton text={f.suggestion} />
        </div>
        <p className="text-sm leading-relaxed whitespace-pre-wrap">{f.suggestion}</p>
      </div>
    </article>
  );
}

function TurnFooter({ turn }: { turn: Turn }) {
  const [open, setOpen] = useState(false);
  const r = turn.result, v = turn.verified;
  return (
    <div className="border-t pt-2">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="text-muted-foreground hover:text-foreground inline-flex cursor-pointer items-center gap-1 text-xs">
        <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} />
        agent 过程（{turn.steps.length} 步）
        {r && v && <span className="ml-2">共 {fmtMs(r.latency_ms + v.latency_ms)} · {fmtUsd(r.cost_usd + v.cost_usd)}</span>}
      </button>
      {open && <div className="mt-2"><TraceSteps steps={turn.steps} /></div>}
    </div>
  );
}

export default function ReviewPage() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [tenant, setTenant] = useState("huaxin");
  const [contract, setContract] = useState("");
  const [editing, setEditing] = useState(true);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [open, setOpen] = useState<{ turnId: number; cid: string } | null>(null);
  const [activeClause, setActiveClause] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState(""); // 服务端在首轮审查时签发
  const bottom = useRef<HTMLDivElement>(null);
  const busy = turns.some((t) => !t.done);

  useEffect(() => {
    getJSON<Tenant[]>("/api/tenants").then(setTenants).catch(() => setTenants([]));
    getJSON<Sample[]>("/api/samples").then(setSamples).catch(() => setSamples([]));
  }, []);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length]);

  // 最近一次完整审查（首轮或换视角重审）的意见，用于合同高亮
  const lastReview = [...turns].reverse().find((t) => t.result?.kind === "review");
  const findings = lastReview?.verified?.findings.length ? lastReview.verified.findings : (lastReview?.result?.findings ?? []);

  const run = (body: { contract?: string; question?: string }, question = "") => {
    const id = Date.now();
    setTurns((ts) => [...ts, { id, question, steps: [], done: false }]);
    const patch = (f: (t: Turn) => Turn) => setTurns((ts) => ts.map((t) => (t.id === id ? f(t) : t)));
    review({ ...body, tenant, session_id: body.question ? sessionId : undefined }, (e) => {
      if (e.event === "session") setSessionId(e.data.session_id);
      else if (e.event === "step") patch((t) => ({ ...t, steps: [...t.steps, e.data] }));
      else if (e.event === "review") patch((t) => ({ ...t, result: e.data }));
      else if (e.event === "verified") patch((t) => ({ ...t, verified: e.data }));
      else if (e.event === "error") patch((t) => ({ ...t, error: e.data.message }));
      else if (e.event === "done") patch((t) => ({ ...t, done: true }));
    });
  };

  const start = () => {
    if (!contract.trim() || busy) return;
    // 每份合同一个新会话，id 由服务端在 session 事件里下发
    setSessionId("");
    setTurns([]);
    setOpen(null);
    setActiveClause(null);
    setEditing(false);
    run({ contract });
  };

  const ask = (q: string) => {
    const question = q.trim();
    if (!question || busy) return;
    setInput("");
    run({ question }, question);
  };

  const pickClause = (no: string) => {
    setActiveClause(no);
    document.getElementById(`clause-${no}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const openTurn = turns.find((t) => t.id === open?.turnId);
  const citation = open && openTurn?.result?.citations[open.cid];
  const tenantName = tenants.find((s) => s.id === tenant)?.name ?? tenant;

  return (
    <div className="flex h-dvh flex-col">
      <TopNav />
      <div className={cn("grid min-h-0 flex-1 grid-cols-1", citation ? "xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(380px,32%)]" : "lg:grid-cols-2")}>
        {/* 左：合同 */}
        <section className="flex min-h-0 flex-col border-r" aria-label="合同原文">
          <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
            <FileText className="text-muted-foreground size-4" />
            <label className="sr-only" htmlFor="tenant">我方公司</label>
            <select id="tenant" value={tenant} disabled={!editing}
              onChange={(e) => setTenant(e.target.value)}
              className="bg-background h-8 cursor-pointer rounded-md border px-2 text-sm disabled:opacity-60">
              {tenants.map((s) => <option key={s.id} value={s.id}>我方：{s.name}</option>)}
            </select>
            {editing ? (
              <>
                <label className="sr-only" htmlFor="sample">示例合同</label>
                <select id="sample" value="" onChange={(e) => {
                  const s = samples.find((x) => x.id === e.target.value);
                  if (s) { setTenant(s.tenant); setContract(s.text); }
                }} className="bg-background h-8 max-w-56 cursor-pointer rounded-md border px-2 text-sm">
                  <option value="">载入示例合同（构造）…</option>
                  {samples.map((s) => <option key={s.id} value={s.id}>{s.id} · {s.title}</option>)}
                </select>
                <button onClick={start} disabled={!contract.trim() || busy}
                  className="bg-primary text-primary-foreground ml-auto inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md px-3 text-sm disabled:cursor-not-allowed disabled:opacity-40">
                  <ShieldCheck className="size-4" />开始审查
                </button>
              </>
            ) : (
              <button onClick={() => { setEditing(true); setTurns([]); setOpen(null); setActiveClause(null); setSessionId(""); }} disabled={busy}
                className="hover:bg-accent ml-auto inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md border px-3 text-sm disabled:opacity-40">
                <Pencil className="size-3.5" />换一份合同
              </button>
            )}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            {editing ? (
              <>
                <label className="sr-only" htmlFor="contract">合同全文</label>
                <textarea id="contract" value={contract} maxLength={20000}
                  onChange={(e) => setContract(e.target.value)}
                  placeholder={`粘贴合同全文（条款以"第一条""第二条"开头），或从上方载入一份构造的示例合同。\n审查立场：${tenantName}。`}
                  className="bg-background focus-visible:ring-ring h-full min-h-80 w-full resize-none rounded-md border p-3 text-sm leading-7 outline-none focus-visible:ring-2" />
              </>
            ) : (
              <>
                {lastReview?.result && (
                  <p className="text-muted-foreground mb-2 text-xs">
                    高亮按“我方是{lastReview.result.our_side}方”的审查结果
                    {turns.filter((t) => t.result?.kind === "review").length > 1 && "（已按追问重新审查）"}
                  </p>
                )}
                <ContractView text={contract} findings={findings} active={activeClause} onPick={pickClause} />
              </>
            )}
          </div>
        </section>

        {/* 中：审查意见与追问 */}
        <main className="flex min-h-0 flex-col" aria-label="审查意见">
          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="space-y-5 px-4 py-5">
              {turns.length === 0 && (
                <div className="space-y-3 py-8">
                  <h1 className="text-2xl font-semibold tracking-tight">站在{tenantName.replace("（构造）", "")}的立场审合同</h1>
                  <p className="text-muted-foreground text-sm leading-relaxed">
                    逐条找出对我方不利的条款，给出风险类型、等级和可直接替换的修改建议；每条意见都挂着《民法典》、合同编通则解释、民事诉讼法或本公司审查规范的原文，点编号看原文。单价会经 DataPilot 只读查询供应商台账比对历史价。依据核实不了的意见不删除，标“转法务复核”。
                  </p>
                  <p className="text-muted-foreground text-xs">演示数据（示例合同、三家公司及其规范、供应商台账）均为构造。</p>
                </div>
              )}
              {turns.map((t) => (
                <section key={t.id} className="space-y-3">
                  {t.question && (
                    <div className="flex justify-end">
                      <p className="bg-primary/10 max-w-[85%] rounded-2xl rounded-br-sm px-4 py-2 text-sm">{t.question}</p>
                    </div>
                  )}
                  {t.error && <p className="text-destructive text-sm">出错了：{t.error}</p>}
                  {!t.done && (
                    <p className="text-muted-foreground inline-flex items-center gap-2 text-sm">
                      <Loader2 className="size-4 animate-spin" />
                      {t.result ? "正在核对每条意见的依据…" : t.steps.length ? `第 ${t.steps.length} 步完成：已检索 ${t.steps.flatMap((s) => s.tool_calls).length} 次，继续审查…` : "通读合同、检索依据中…"}
                    </p>
                  )}
                  {t.result?.kind === "clarify" && (
                    <div className="bg-card space-y-3 rounded-xl border p-4">
                      <p className="text-sm">{t.result.clarification}</p>
                      <div className="flex gap-2">
                        {["甲", "乙"].map((s) => (
                          <button key={s} onClick={() => ask(`我方是${s}方。`)} disabled={busy}
                            className="hover:bg-accent cursor-pointer rounded-full border px-3 py-1.5 text-sm disabled:opacity-40">我方是{s}方</button>
                        ))}
                      </div>
                    </div>
                  )}
                  {t.result?.kind === "review" && (
                    <>
                      <div className="flex flex-wrap items-center gap-2 text-sm">
                        <span className="font-medium">我方：{t.result.our_side}方</span>
                        {(["高", "中", "低"] as const).map((l) => {
                          const n = (t.verified?.findings ?? t.result!.findings).filter((f) => f.level === l).length;
                          return n ? <span key={l} className={cn("rounded px-1.5 py-0.5 text-xs", LEVEL[l].badge)}>{l} {n}</span> : null;
                        })}
                        {t.verified && t.verified.flagged > 0 && (
                          <span className="rounded bg-sky-500/15 px-1.5 py-0.5 text-xs text-sky-700 dark:text-sky-400">校验转人工 {t.verified.flagged}</span>
                        )}
                      </div>
                      <p className="text-muted-foreground text-sm whitespace-pre-line">{t.result.summary}</p>
                      {(t.verified?.findings.length ? t.verified.findings : t.result.findings).map((f, i) => (
                        <FindingCard key={i} f={f} i={i} result={t.result!} active={open?.turnId === t.id ? open.cid : null}
                          onCite={(cid) => setOpen({ turnId: t.id, cid })} onClause={pickClause} />
                      ))}
                      {t.result.findings.length === 0 && <p className="text-sm">没有发现对我方明显不利的条款。</p>}
                    </>
                  )}
                  {t.result?.kind === "followup" && (
                    <div className="bg-card space-y-2 rounded-xl border p-4 text-sm">
                      {t.result.answer_clause && (
                        <button onClick={() => pickClause(t.result!.answer_clause!)} className="cursor-pointer font-medium underline-offset-4 hover:underline">{t.result.answer_clause}</button>
                      )}
                      <p className="leading-relaxed whitespace-pre-wrap">
                        {t.result.answer}
                        {t.result.answer_citations.map((c) => (
                          <CiteChip key={c} id={c} citation={t.result!.citations[c]} active={open?.turnId === t.id && open.cid === c}
                            onOpen={(cid) => setOpen({ turnId: t.id, cid })} />
                        ))}
                      </p>
                      {t.result.answer_needs_human && <p className="text-xs text-sky-700 dark:text-sky-400">建议转法务复核。</p>}
                    </div>
                  )}
                  {t.result?.kind === "fallback" && <p className="text-sm">这次没有得到完整的审查结果，请重试或转人工审查。</p>}
                  {(t.steps.length > 0 || t.result) && <TurnFooter turn={t} />}
                </section>
              ))}
              <div ref={bottom} />
            </div>
          </div>
          {!editing && (
            <form onSubmit={(e) => { e.preventDefault(); ask(input); }} className="border-t p-3">
              <div className="flex items-end gap-2">
                <label className="sr-only" htmlFor="q">追问</label>
                <textarea id="q" value={input} maxLength={2000} rows={1}
                  placeholder="追问，例如：你列的第 2 条意见怎么改？ / 换成乙方视角再看一遍"
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); ask(input); }
                  }}
                  className="bg-background focus-visible:ring-ring min-h-10 flex-1 resize-none rounded-md border px-3 py-2 text-sm outline-none focus-visible:ring-2" />
                <button type="submit" disabled={busy || !input.trim() || !lastReview && !turns.some((t) => t.result?.kind === "clarify")} aria-label="发送"
                  className="bg-primary text-primary-foreground inline-flex h-10 w-10 cursor-pointer items-center justify-center rounded-md disabled:cursor-not-allowed disabled:opacity-40">
                  {busy ? <Loader2 className="size-4 animate-spin" /> : <SendHorizonal className="size-4" />}
                </button>
              </div>
            </form>
          )}
        </main>

        {citation && open && (
          // 宽屏是第三栏；窄屏浮成底部抽屉，避免被挤成几行高
          <div className="bg-card fixed inset-x-0 bottom-0 z-20 h-[65vh] border-t shadow-2xl xl:static xl:z-auto xl:h-auto xl:min-h-0 xl:overflow-hidden xl:border-t-0 xl:shadow-none">
            {citation.kind === "doc" ? (
              <SourceViewer id={open.cid} citation={citation} onClose={() => setOpen(null)} />
            ) : (
              <TableCitationView id={open.cid} c={citation} onClose={() => setOpen(null)} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

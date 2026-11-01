"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { EChart } from "@/components/echart";
import { Metric } from "@/components/metric";
import {
  ContractRetrievalView,
  ReviewD4View,
  ReviewDialogView,
  ReviewLongDialogView,
  ReviewView,
} from "@/components/review-eval";
import { TopNav } from "@/components/top-nav";
import { TraceDrawer } from "@/components/trace-drawer";
import { getJSON, pct, type RunDetail, type RunInfo } from "@/lib/api";
import { cn } from "@/lib/utils";

type Row = Record<string, unknown>;
type ChartTheme = { series: string[]; axis: string; split: string };
const KIND_LABEL = {
  review: "合同审查",
  review_d4: "合同·依据校验",
  review_dialog: "合同·多轮追问",
  review_longdialog: "合同·长对话记忆",
  contract_retrieval: "合同·检索",
  retrieval: "高校·检索（D2）",
  agent: "高校·Agent（D3）",
  d4: "高校·校验与拒答（D4）",
  dialog: "高校·多轮对话记忆",
} as const;
const MODE_LABEL: Record<string, string> = { none: "不带记忆", full: "完整历史", compress: "历史 + 压缩" };

function DialogView({ s }: { s: Record<string, unknown> }) {
  const modes = ["none", "full", "compress"].filter((m) => s[m]);
  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-muted-foreground bg-muted/40 border-b text-left text-xs">
            <th className="px-3 py-2 font-medium">记忆方式</th>
            <th className="font-medium">追问正确率</th>
            <th className="font-medium">指代</th>
            <th className="font-medium">省略</th>
            <th className="font-medium">槽位延续</th>
            <th className="font-medium">换话题</th>
            <th className="font-medium">平均历史字数</th>
            <th className="font-medium">平均输入 token</th>
            <th className="px-3 font-medium">压缩次数</th>
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {modes.map((m) => (
            <tr key={m} className="border-b last:border-0">
              <td className="px-3 py-1.5">{MODE_LABEL[m]}</td>
              <td className="font-medium">{pct(get(s, `${m}.followup_correct`))}（{String(get(s, `${m}.followup_n`))}）</td>
              {["coreference", "ellipsis", "slot_carry", "topic_switch"].map((p) => (
                <td key={p}>{pct(get(s, `${m}.by_pattern.${p}.correct`))}</td>
              ))}
              <td>{Math.round(num(get(s, `${m}.history_tokens_mean`)))}</td>
              <td>{Math.round(num(get(s, `${m}.input_tokens_mean`)))}</td>
              <td className="px-3">{String(get(s, `${m}.compressions`) ?? 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
const KIND_TEXT: Record<string, string> = {
  answer: "作答",
  partial: "部分",
  refuse: "拒答",
  clarify: "反问",
  fallback: "未完成",
};
const num = (x: unknown) => (typeof x === "number" ? x : 0);
const get = (o: unknown, path: string) =>
  path.split(".").reduce<unknown>((a, k) => (a as Record<string, unknown> | undefined)?.[k], o);

function RetrievalView({ d }: { d: RunDetail }) {
  const configs = useMemo(
    () => Object.entries(d.summary).filter(([k]) => !k.startsWith("_")) as [string, Record<string, unknown>][],
    [d],
  );
  const option = useCallback(
    (t: ChartTheme) => ({
      grid: { left: 190, right: 20, top: 10, bottom: 30 },
      xAxis: {
        type: "value",
        min: 0.6,
        max: 1,
        axisLabel: { formatter: (v: number) => `${Math.round(v * 100)}%` },
        splitLine: { lineStyle: { color: t.split } },
      },
      yAxis: { type: "category", data: configs.map(([k]) => k), axisLine: { lineStyle: { color: t.axis } } },
      series: [
        {
          type: "bar",
          data: configs.map(([, s]) => get(s, "overall.recall@5")),
          itemStyle: { color: t.series[0] },
          barMaxWidth: 14,
        },
      ],
      tooltip: { trigger: "axis", valueFormatter: (v: number) => pct(v) },
    }),
    [configs],
  );
  return (
    <div className="space-y-4">
      <EChart option={option} height={Math.max(220, configs.length * 26)} label="各检索配置 Recall@5 条形图" />
      <table className="w-full text-sm">
        <thead>
          <tr className="text-muted-foreground border-b text-left text-xs">
            <th className="py-2 font-medium">配置</th>
            <th className="font-medium">R@1</th>
            <th className="font-medium">R@5</th>
            <th className="font-medium">R@10</th>
            <th className="font-medium">MRR</th>
            <th className="font-medium">均耗时</th>
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {configs.map(([k, s]) => (
            <tr key={k} className="border-b last:border-0">
              <td className="py-1.5 font-mono text-xs">{k}</td>
              <td>{pct(get(s, "overall.recall@1"))}</td>
              <td className="font-medium">{pct(get(s, "overall.recall@5"))}</td>
              <td>{pct(get(s, "overall.recall@10"))}</td>
              <td>{num(get(s, "overall.mrr")).toFixed(3)}</td>
              <td>{Math.round(num(s.latency_ms_mean))}ms</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const D4_KEYS = [
  ["citation_accuracy", "引用准确率"],
  ["answerable.correct", "答案正确"],
  ["refuse.lenient_rate", "拒答正确"],
  ["hallucination_rate", "幻觉率"],
  ["answerable.false_refusal", "误拒率"],
] as const;

function D4Metrics({ s }: { s: Record<string, unknown> }) {
  const v = (k: string) => pct(get(s, `${k}.verified`));
  const r = (k: string) => pct(get(s, `${k}.raw`));
  const option = useCallback(
    (t: ChartTheme) => ({
      legend: { data: ["校验前", "校验后"], top: 0 },
      grid: { left: 40, right: 10, top: 30, bottom: 30 },
      xAxis: { type: "category", data: D4_KEYS.map(([, l]) => l), axisLine: { lineStyle: { color: t.axis } } },
      yAxis: {
        type: "value",
        max: 1,
        axisLabel: { formatter: (x: number) => `${Math.round(x * 100)}%` },
        splitLine: { lineStyle: { color: t.split } },
      },
      series: [
        { name: "校验前", type: "bar", data: D4_KEYS.map(([k]) => get(s, `${k}.raw`)), itemStyle: { color: t.series[1] }, barMaxWidth: 22 },
        { name: "校验后", type: "bar", data: D4_KEYS.map(([k]) => get(s, `${k}.verified`)), itemStyle: { color: t.series[0] }, barMaxWidth: 22 },
      ],
      tooltip: { trigger: "axis", valueFormatter: (x: number) => pct(x) },
    }),
    [s],
  );
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Metric label="引用准确率（句级）" value={v("citation_accuracy")} before={r("citation_accuracy")} hint="目标 ≥90%"
          good={num(get(s, "citation_accuracy.verified")) >= 0.9} />
        <Metric label="答案正确率" value={v("answerable.correct")} before={r("answerable.correct")}
          hint={`含部分正确 ${pct(get(s, "answerable.correct_or_partial.verified"))}`} />
        <Metric label="拒答正确率（宽口径）" value={v("refuse.lenient_rate")} before={r("refuse.lenient_rate")}
          hint={`严格 ${pct(get(s, "refuse.strict_rate.verified"))} · 目标 ≥90%`} good={num(get(s, "refuse.lenient_rate.verified")) >= 0.9} />
        <Metric label="幻觉率" value={v("hallucination_rate")} before={r("hallucination_rate")} hint="目标 ≤5%"
          good={num(get(s, "hallucination_rate.verified")) <= 0.05} />
        <Metric label="误拒率" value={v("answerable.false_refusal")} before={r("answerable.false_refusal")} />
      </div>
      <EChart option={option} label="校验前后各项指标对比柱状图" />
    </div>
  );
}

function AgentMetrics({ s }: { s: Record<string, unknown> }) {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
      <Metric label="作答率" value={pct(get(s, "answerable.answer_rate"))} hint={`误拒 ${pct(get(s, "answerable.false_refusal_rate"))}`} />
      <Metric label="引用命中率" value={pct(get(s, "answerable.cite_hit_rate"))} />
      <Metric label="查表路由" value={pct(get(s, "table.route_rate"))} />
      <Metric label="拒答（严格口径）" value={pct(get(s, "refuse.correct_refuse_rate"))} />
      <Metric label="耗时 P50" value={`${(num(get(s, "latency_ms.p50")) / 1000).toFixed(1)}s`}
        hint={`P95 ${(num(get(s, "latency_ms.p95")) / 1000).toFixed(1)}s`} />
    </div>
  );
}

function rowFlags(kind: RunInfo["kind"], r: Row): { text: string; bad: boolean }[] {
  if (kind === "d4") {
    return [
      { text: `${KIND_TEXT[String(r.raw_kind)] ?? r.raw_kind}→${KIND_TEXT[String(r.ver_kind)] ?? r.ver_kind}`, bad: false },
      { text: r.type === "refuse" ? (r.ver_refusal_ok || r.ver_kind === "refuse" ? "守住边界" : "越界") : String(r.ver_correct ?? ""),
        bad: r.type === "refuse" ? !(r.ver_refusal_ok || r.ver_kind === "refuse") : r.ver_correct !== "correct" },
      { text: r.ver_hallucinated ? "幻觉" : "", bad: Boolean(r.ver_hallucinated) },
      { text: num(r.dropped) ? `删${r.dropped}句` : "", bad: false },
    ];
  }
  return [
    { text: KIND_TEXT[String(r.kind)] ?? String(r.kind ?? ""), bad: false },
    { text: r.type === "refuse" ? "" : r.cite_hit ? "引用命中" : "未命中", bad: !r.cite_hit && r.type !== "refuse" },
  ];
}

function Flags({ kind, r }: { kind: RunInfo["kind"]; r: Row }) {
  return (
    <>
      {rowFlags(kind, r)
        .filter((f) => f.text)
        .map((f, i) => (
          <span key={i} className={cn("mr-1.5", f.bad ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground")}>
            {f.text}
          </span>
        ))}
    </>
  );
}

function QuestionTable({ d, compare, onOpen }: { d: RunDetail; compare?: RunDetail; onOpen: (id: string) => void }) {
  const [filter, setFilter] = useState("all");
  const other = useMemo(() => new Map((compare?.rows ?? []).map((r) => [r.qid as string, r])), [compare]);
  const rows = d.rows.filter((r) => filter === "all" || r.type === filter);
  return (
    <div className="space-y-2">
      <div className="flex gap-1 text-xs" role="tablist" aria-label="题型筛选">
        {(["all", "answerable", "table", "refuse"] as const).map((f) => (
          <button key={f} role="tab" aria-selected={filter === f} onClick={() => setFilter(f)}
            className={cn("cursor-pointer rounded-md px-2.5 py-1", filter === f ? "bg-accent" : "text-muted-foreground hover:text-foreground")}>
            {{ all: "全部", answerable: "可回答", table: "需查表", refuse: "该拒答" }[f]}
          </button>
        ))}
        <span className="text-muted-foreground ml-auto self-center">{rows.length} 题 · 点击一行回放 trace</span>
      </div>
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-muted-foreground bg-muted/40 border-b text-left text-xs">
              <th className="px-3 py-2 font-medium">题</th>
              <th className="font-medium">问题</th>
              <th className="font-medium">结果</th>
              {compare && <th className="font-medium">对比运行</th>}
              <th className="px-3 text-right font-medium">耗时</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const o = other.get(r.qid as string);
              return (
                <tr key={r.qid as string} onClick={() => r.trace_id && onOpen(r.trace_id as string)}
                  className="hover:bg-accent/50 cursor-pointer border-b last:border-0">
                  <td className="px-3 py-1.5 font-mono text-xs whitespace-nowrap">{r.qid as string}</td>
                  <td className="max-w-md truncate pr-2" title={r.question as string}>{r.question as string}</td>
                  <td className="text-xs whitespace-nowrap"><Flags kind={d.kind} r={r} /></td>
                  {compare && <td className="text-xs whitespace-nowrap">{o ? <Flags kind={compare.kind} r={o} /> : "—"}</td>}
                  <td className="text-muted-foreground px-3 text-right text-xs tabular-nums">{(num(r.latency_ms) / 1000).toFixed(1)}s</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function EvalPage() {
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [sel, setSel] = useState("");
  const [cmp, setCmp] = useState("");
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [cmpDetail, setCmpDetail] = useState<RunDetail | undefined>();
  const [trace, setTrace] = useState<string | null>(null);

  useEffect(() => {
    getJSON<RunInfo[]>("/api/runs").then((rs) => {
      setRuns(rs);
      // 优先看最新的全量合同审查（48 份），其次高校场景的全量 D4
      const full = (k: RunInfo["kind"], n: number) => rs.find((r) => r.kind === k && (r.n ?? 0) >= n);
      const pref = full("review", 48) ?? full("d4", 100) ?? rs[0];
      if (pref) setSel(pref.run);
    });
  }, []);
  useEffect(() => {
    if (!sel) return;
    setDetail(null);
    setCmp("");
    getJSON<RunDetail>(`/api/runs/${encodeURIComponent(sel)}`).then(setDetail);
  }, [sel]);
  useEffect(() => {
    if (!cmp) {
      setCmpDetail(undefined);
      return;
    }
    getJSON<RunDetail>(`/api/runs/${encodeURIComponent(cmp)}`).then(setCmpDetail);
  }, [cmp]);

  const sameKind = runs.filter((r) => detail && r.kind === detail.kind && r.run !== sel);
  return (
    <div className="flex min-h-dvh flex-col">
      <TopNav />
      <main className="mx-auto w-full max-w-6xl space-y-6 px-4 py-6">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <h1 className="text-xl font-semibold">评测看板</h1>
            <p className="text-muted-foreground text-sm">合同场景：48 份构造合同；高校场景（方法验证）的运行也保留在列表里。每次运行的指标与 trace 都可回放</p>
          </div>
          <div className="ml-auto flex flex-wrap gap-2 text-sm">
            <label className="flex items-center gap-1.5">
              <span className="text-muted-foreground text-xs">运行</span>
              <select value={sel} onChange={(e) => setSel(e.target.value)} className="bg-background h-9 cursor-pointer rounded-md border px-2">
                {(["review", "review_d4", "review_dialog", "review_longdialog", "contract_retrieval", "d4", "dialog", "agent", "retrieval"] as const).map((k) => (
                  <optgroup key={k} label={KIND_LABEL[k]}>
                    {runs.filter((r) => r.kind === k).map((r) => (
                      <option key={r.run} value={r.run}>
                        {r.run}（{r.n ?? "?"}）
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </label>
            {detail && (detail.kind === "agent" || detail.kind === "d4") && (
              <label className="flex items-center gap-1.5">
                <span className="text-muted-foreground text-xs">对比</span>
                <select value={cmp} onChange={(e) => setCmp(e.target.value)} className="bg-background h-9 cursor-pointer rounded-md border px-2">
                  <option value="">不对比</option>
                  {sameKind.map((r) => (
                    <option key={r.run} value={r.run}>{r.run}</option>
                  ))}
                </select>
              </label>
            )}
          </div>
        </div>
        {!detail ? (
          <p className="text-muted-foreground text-sm">加载中…</p>
        ) : (
          <>
            {detail.kind === "retrieval" && <RetrievalView d={detail} />}
            {detail.kind === "d4" && <D4Metrics s={detail.summary} />}
            {detail.kind === "agent" && <AgentMetrics s={detail.summary} />}
            {detail.kind === "dialog" && <DialogView s={detail.summary} />}
            {detail.kind === "review" && <ReviewView d={detail} onOpen={setTrace} />}
            {detail.kind === "review_d4" && <ReviewD4View d={detail} />}
            {detail.kind === "review_dialog" && <ReviewDialogView d={detail} onOpen={setTrace} />}
            {detail.kind === "contract_retrieval" && <ContractRetrievalView d={detail} />}
            {detail.kind === "review_longdialog" && <ReviewLongDialogView d={detail} onOpen={setTrace} />}
            {(detail.kind === "agent" || detail.kind === "d4") && (
              <QuestionTable d={detail} compare={cmpDetail} onOpen={setTrace} />
            )}
          </>
        )}
      </main>
      {trace && detail && <TraceDrawer run={detail.run} traceId={trace} onClose={() => setTrace(null)} />}
    </div>
  );
}

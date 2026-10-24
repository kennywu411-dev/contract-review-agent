"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { EChart } from "@/components/echart";
import { Metric } from "@/components/metric";
import { TopNav } from "@/components/top-nav";
import { TraceDrawer } from "@/components/trace-drawer";
import { fmtMs, fmtUsd, getJSON, type RunInfo, type StageRow } from "@/lib/api";

type ChartTheme = { series: string[]; axis: string; split: string };

const quantile = (xs: number[], p: number) => {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(p * s.length))];
};
const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);

const STAGES = [
  ["llm_ms", "模型调用"],
  ["search_ms", "检索 + 重排"],
  ["sql_ms", "查表（MCP）"],
  ["verify_ms", "引用校验"],
] as const;

export default function CostPage() {
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [sel, setSel] = useState("");
  const [rows, setRows] = useState<StageRow[]>([]);
  const [trace, setTrace] = useState<string | null>(null);

  useEffect(() => {
    getJSON<RunInfo[]>("/api/runs").then((rs) => {
      // 只列有逐题 trace 的运行（检索评测和依据校验没有）
      const usable = rs.filter((r) => !["retrieval", "contract_retrieval", "review_d4"].includes(r.kind));
      setRuns(usable);
      const full = (k: RunInfo["kind"], n: number) => usable.find((r) => r.kind === k && (r.n ?? 0) >= n);
      const pref = full("review", 48) ?? full("d4", 100) ?? full("agent", 100) ?? usable[0];
      if (pref) setSel(pref.run);
    });
  }, []);
  useEffect(() => {
    if (!sel) return;
    setRows([]);
    getJSON<{ rows: StageRow[] }>(`/api/runs/${encodeURIComponent(sel)}/stages`).then((d) => setRows(d.rows));
  }, [sel]);

  const stats = useMemo(() => {
    const cost = rows.map((r) => r.cost_usd);
    const lat = rows.map((r) => r.latency_ms);
    const cacheRead = rows.reduce((a, r) => a + r.cache_read, 0);
    const cacheWrite = rows.reduce((a, r) => a + r.cache_write, 0);
    const input = rows.reduce((a, r) => a + r.input, 0);
    return {
      total: cost.reduce((a, b) => a + b, 0),
      costP50: quantile(cost, 0.5),
      costP95: quantile(cost, 0.95),
      latP50: quantile(lat, 0.5),
      latP95: quantile(lat, 0.95),
      cacheRead,
      cacheWrite,
      input,
      steps: mean(rows.map((r) => r.steps)),
    };
  }, [rows]);

  const stageOption = useCallback(
    (t: ChartTheme) => ({
      grid: { left: 90, right: 20, top: 10, bottom: 30 },
      xAxis: { type: "value", axisLabel: { formatter: (v: number) => `${(v / 1000).toFixed(1)}s` }, splitLine: { lineStyle: { color: t.split } } },
      yAxis: { type: "category", data: STAGES.map(([, l]) => l), axisLine: { lineStyle: { color: t.axis } } },
      series: [
        {
          type: "bar",
          data: STAGES.map(([k]) => mean(rows.map((r) => r[k]))),
          itemStyle: { color: t.series[0] },
          barMaxWidth: 18,
        },
      ],
      tooltip: { trigger: "axis", valueFormatter: (v: number) => fmtMs(v) },
    }),
    [rows],
  );

  const scatterOption = useCallback(
    (t: ChartTheme) => ({
      grid: { left: 60, right: 20, top: 10, bottom: 40 },
      xAxis: { type: "value", name: "耗时（秒）", nameLocation: "middle", nameGap: 26, splitLine: { lineStyle: { color: t.split } } },
      yAxis: { type: "value", name: "花费（美元）", axisLabel: { formatter: (v: number) => v.toFixed(4) }, splitLine: { lineStyle: { color: t.split } } },
      series: [
        {
          type: "scatter",
          symbolSize: 7,
          data: rows.map((r) => [r.latency_ms / 1000, r.cost_usd, r.question]),
          itemStyle: { color: t.series[0], opacity: 0.75 },
        },
      ],
      tooltip: {
        trigger: "item",
        formatter: (p: { data: [number, number, string] }) => `${p.data[2]}<br/>${p.data[0].toFixed(1)}s · $${p.data[1].toFixed(5)}`,
      },
    }),
    [rows],
  );

  const totalIn = stats.input + stats.cacheRead + stats.cacheWrite;
  const slowest = [...rows].sort((a, b) => b.latency_ms - a.latency_ms).slice(0, 10);
  return (
    <div className="flex min-h-dvh flex-col">
      <TopNav />
      <main className="mx-auto w-full max-w-6xl space-y-6 px-4 py-6">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <h1 className="text-xl font-semibold">成本与延迟</h1>
            <p className="text-muted-foreground text-sm">每题的花费和耗时来自 trace（模型 token、工具耗时、校验）；评判花费不计入</p>
          </div>
          <label className="ml-auto flex items-center gap-1.5 text-sm">
            <span className="text-muted-foreground text-xs">运行</span>
            <select value={sel} onChange={(e) => setSel(e.target.value)} className="bg-background h-9 cursor-pointer rounded-md border px-2">
              {runs.map((r) => (
                <option key={r.run} value={r.run}>
                  {r.run}（{r.n ?? "?"} 题）
                </option>
              ))}
            </select>
          </label>
        </div>
        {!rows.length ? (
          <p className="text-muted-foreground text-sm">加载中…</p>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
              <Metric label={`总花费（${rows.length} 题）`} value={fmtUsd(stats.total)} />
              <Metric label="单题花费 P50" value={fmtUsd(stats.costP50)} hint={`P95 ${fmtUsd(stats.costP95)}`} />
              <Metric label="单题耗时 P50" value={fmtMs(stats.latP50)} hint={`P95 ${fmtMs(stats.latP95)} · 目标 P50≤10s`} good={stats.latP50 <= 10_000} />
              <Metric label="平均步数" value={stats.steps.toFixed(1)} />
              <Metric
                label="输入 token 构成"
                value={`${Math.round((stats.cacheRead / Math.max(totalIn, 1)) * 100)}% 读缓存`}
                hint={`写缓存 ${Math.round((stats.cacheWrite / Math.max(totalIn, 1)) * 100)}% · 未缓存 ${Math.round((stats.input / Math.max(totalIn, 1)) * 100)}%`}
              />
            </div>
            <div className="grid gap-6 md:grid-cols-2">
              <section className="space-y-2">
                <h2 className="text-sm font-medium">平均耗时构成</h2>
                <EChart option={stageOption} height={220} label="各环节平均耗时条形图" />
              </section>
              <section className="space-y-2">
                <h2 className="text-sm font-medium">每题花费 × 耗时</h2>
                <EChart option={scatterOption} height={220} label="每题花费与耗时散点图" />
              </section>
            </div>
            <section className="space-y-2">
              <h2 className="text-sm font-medium">最慢的 10 题</h2>
              <div className="overflow-x-auto rounded-lg border">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-muted-foreground bg-muted/40 border-b text-left text-xs">
                      <th className="px-3 py-2 font-medium">问题</th>
                      <th className="font-medium">步数</th>
                      {STAGES.map(([k, l]) => (
                        <th key={k} className="font-medium">{l}</th>
                      ))}
                      <th className="font-medium">总耗时</th>
                      <th className="px-3 text-right font-medium">花费</th>
                    </tr>
                  </thead>
                  <tbody className="tabular-nums">
                    {slowest.map((r) => (
                      <tr key={r.trace_id} onClick={() => setTrace(r.trace_id)} className="hover:bg-accent/50 cursor-pointer border-b last:border-0">
                        <td className="max-w-xs truncate px-3 py-1.5" title={r.question}>{r.question}</td>
                        <td>{r.steps}</td>
                        {STAGES.map(([k]) => (
                          <td key={k} className="text-xs">{fmtMs(r[k])}</td>
                        ))}
                        <td className="font-medium">{fmtMs(r.latency_ms)}</td>
                        <td className="px-3 text-right text-xs">{fmtUsd(r.cost_usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </main>
      {trace && <TraceDrawer run={sel} traceId={trace} onClose={() => setTrace(null)} />}
    </div>
  );
}

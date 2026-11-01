"use client";

import { useCallback, useMemo } from "react";

import { EChart } from "@/components/echart";
import { Metric } from "@/components/metric";
import { fmtMs, fmtUsd, pct, RISK_TYPES, type RunDetail } from "@/lib/api";
import { cn } from "@/lib/utils";

type Row = Record<string, unknown>;
const num = (x: unknown) => (typeof x === "number" ? x : 0);
const get = (o: unknown, path: string) =>
  path.split(".").reduce<unknown>((a, k) => (a && typeof a === "object" ? (a as Row)[k] : undefined), o);

/** 合同审查运行：核心指标、11 类风险检出率、逐份结果（点开看 trace）。 */
export function ReviewView({ d, onOpen }: { d: RunDetail; onOpen: (traceId: string) => void }) {
  const s = d.summary;
  const byType = useMemo(
    () => (get(s, "by_type") ?? {}) as Record<string, { n: number; recall: number; type_ok: number }>,
    [s],
  );
  const keys = useMemo(() => Object.keys(byType), [byType]);
  const option = useCallback(
    (t: { series: string[]; axis: string; split: string }) => ({
      legend: { data: ["检出率", "类型判对"], top: 0 },
      grid: { left: 40, right: 10, top: 30, bottom: 10, containLabel: true },
      xAxis: {
        type: "category",
        data: keys.map((k) => RISK_TYPES[k] ?? k),
        axisLabel: { rotate: 35, fontSize: 10 },
        axisLine: { lineStyle: { color: t.axis } },
      },
      yAxis: { type: "value", max: 1, axisLabel: { formatter: (x: number) => `${Math.round(x * 100)}%` }, splitLine: { lineStyle: { color: t.split } } },
      series: [
        { name: "检出率", type: "bar", data: keys.map((k) => byType[k].recall), itemStyle: { color: t.series[0] }, barMaxWidth: 18 },
        { name: "类型判对", type: "bar", data: keys.map((k) => byType[k].type_ok), itemStyle: { color: t.series[1] }, barMaxWidth: 18 },
      ],
      tooltip: { trigger: "axis", valueFormatter: (x: number) => pct(x) },
    }),
    [keys, byType],
  );
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Metric label="风险检出率" value={pct(get(s, "detection.recall"))} hint={`${num(get(s, "detection.n_gold"))} 处风险`} />
        <Metric label="类型判对 / 依据正确" value={pct(get(s, "detection.type_accuracy"))} hint={`依据 ${pct(get(s, "detection.basis_accuracy"))}`} />
        <Metric label="意见精确率（高/中）" value={pct(get(s, "detection.precision_hm"))} hint={`含低等级 ${pct(get(s, "detection.precision"))}`} />
        <Metric label="干净合同误报（高/中）" value={pct(get(s, "clean.fp_contract_rate_hm"))}
          hint={`${num(get(s, "clean.n"))} 份 · 含低等级 ${pct(get(s, "clean.fp_contract_rate"))}`} good={num(get(s, "clean.fp_contract_rate_hm")) <= 0.3} />
        <Metric label="视角错误" value={pct(get(s, "perspective.favorable_flag_rate"))} hint={`我方判断 ${pct(get(s, "perspective.side_accuracy"))}`} />
        <Metric label="临界条款转人工" value={pct(get(s, "human_review.borderline_human_rate"))} hint={`真风险被标转人工 ${pct(get(s, "human_review.human_rate_on_gold"))}`} />
        <Metric label="反问率" value={pct(get(s, "clarify.clarify_rate"))} hint={`多余反问 ${pct(get(s, "clarify.unnecessary_clarify_rate"))}`} />
        <Metric label="摘录与原文一致" value={pct(get(s, "faithfulness.quote_found_rate"))} />
        <Metric label="单份花费 P50" value={fmtUsd(num(get(s, "cost.p50")))} hint={`合计 ${fmtUsd(num(get(s, "cost.total_usd")))}`} />
        <Metric label="耗时 P50" value={fmtMs(num(get(s, "latency_ms.p50")))} hint={`P95 ${fmtMs(num(get(s, "latency_ms.p95")))}`} />
      </div>
      {keys.length > 0 && <EChart option={option} height={300} label="11 类风险的检出率与类型判对率柱状图" />}
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-muted-foreground bg-muted/40 border-b text-left text-xs">
              <th className="px-3 py-2 font-medium">合同</th>
              <th className="font-medium">类型</th>
              <th className="font-medium">结果</th>
              <th className="font-medium">检出</th>
              <th className="font-medium">误报</th>
              <th className="font-medium">意见数</th>
              <th className="font-medium">花费</th>
              <th className="px-3 font-medium">耗时</th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {d.rows.map((r) => {
              const gold = (r.gold as { detected: boolean }[]) ?? [];
              const hit = gold.filter((g) => g.detected).length;
              const sub = r.clarify ? "反问" : r.clean ? "干净" : "含风险";
              return (
                <tr key={String(r.id)} onClick={() => onOpen(String(r.trace_id))}
                  className="hover:bg-accent/50 cursor-pointer border-b last:border-0">
                  <td className="px-3 py-1.5 font-mono text-xs">{String(r.id)}</td>
                  <td className="text-xs">{String(r.title ?? "")}<span className="text-muted-foreground ml-1">· {sub}</span></td>
                  <td className="text-xs">{String(r.kind)}</td>
                  <td className={cn("text-xs", gold.length && hit < gold.length && "text-amber-600 dark:text-amber-400")}>
                    {gold.length ? `${hit}/${gold.length}` : "—"}
                  </td>
                  <td className={cn("text-xs", num(r.fp) + num(r.favorable_flagged) > 0 && "text-amber-600 dark:text-amber-400")}>
                    {num(r.fp) + num(r.favorable_flagged)}
                  </td>
                  <td className="text-xs">{num(r.n_findings)}</td>
                  <td className="text-xs">{fmtUsd(num(r.cost_usd))}</td>
                  <td className="px-3 text-xs">{fmtMs(num(r.latency_ms))}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** 依据校验运行：校验前后"未拦截的无依据意见"与修改建议质量。 */
export function ReviewD4View({ d }: { d: RunDetail }) {
  const s = d.summary;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Metric label="未拦截的无依据意见" value={pct(get(s, "hallucination.after_unflagged_rate"))}
          before={pct(get(s, "hallucination.raw_unflagged_rate"))} good={num(get(s, "hallucination.after_unflagged_rate")) <= 0.05} />
        <Metric label="理由含无依据说法" value={pct(get(s, "hallucination.raw_rate"))} hint={`${num(get(s, "findings"))} 条意见`} />
        <Metric label="误拦" value={pct(get(s, "hallucination.false_flag_rate"))} hint="有依据却被新标转人工" />
        <Metric label="点名未检索的条文" value={pct(get(s, "unretrieved_ref_rate"))} />
        <Metric label="修改建议均分" value={num(get(s, "suggestion.mean")).toFixed(2)} hint={`≥4 分 ${pct(get(s, "suggestion.ge4_rate"))}`} />
      </div>
      <p className="text-muted-foreground text-sm">
        校验器动作：新标转人工 {num(get(s, "flagged"))} 条、改写理由 {num(get(s, "revised"))} 条、改正引用 {num(get(s, "repaired"))} 条；
        花费 校验 {fmtUsd(num(get(s, "cost.verify_usd")))} · 评判 {fmtUsd(num(get(s, "cost.judge_usd")))}。依据不足的意见不删除，改为转法务复核（ADR 008）。
      </p>
    </div>
  );
}

const DIALOG_TYPES: [string, string][] = [
  ["clarify", "补充我方身份"],
  ["ordinal", "“你列的第 k 条”怎么改"],
  ["basis", "某条的依据"],
  ["flip", "换对方视角重审"],
];

/** 多轮追问运行：带/不带上一轮历史的对比。 */
export function ReviewDialogView({ d, onOpen }: { d: RunDetail; onOpen: (traceId: string) => void }) {
  const s = d.summary;
  const modes = ["full", "none"].filter((m) => s[m]);
  return (
    <div className="space-y-4">
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-muted-foreground bg-muted/40 border-b text-left text-xs">
              <th className="px-3 py-2 font-medium">追问类型</th>
              {modes.map((m) => <th key={m} className="font-medium">{m === "full" ? "带上一轮审查" : "不带历史"}</th>)}
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {DIALOG_TYPES.map(([k, label]) => (
              <tr key={k} className="border-b last:border-0">
                <td className="px-3 py-1.5">{label}</td>
                {modes.map((m) => {
                  const x = get(s, `${m}.${k}`) as Row | undefined;
                  if (!x) return <td key={m}>—</td>;
                  const extra =
                    k === "basis" ? ` · 依据对 ${pct(x.basis_ok_rate)}` : k === "flip" ? ` · 原风险仍报 ${pct(x.old_gold_reported)}` : k === "clarify" ? ` · 检出 ${pct(x.recall)}` : "";
                  return <td key={m} className="text-xs">{pct(x.ok_rate)}（{num(x.n)}）{extra}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-muted-foreground bg-muted/40 border-b text-left text-xs">
              <th className="px-3 py-2 font-medium">合同</th>
              <th className="font-medium">方式</th>
              <th className="font-medium">追问</th>
              <th className="font-medium">结果</th>
              <th className="px-3 font-medium">花费</th>
            </tr>
          </thead>
          <tbody>
            {d.rows.map((r, i) => (
              <tr key={i} onClick={() => onOpen(String(r.trace_id))} className="hover:bg-accent/50 cursor-pointer border-b last:border-0">
                <td className="px-3 py-1.5 font-mono text-xs">{String(r.contract_id)}</td>
                <td className="text-xs">{r.mode === "full" ? "带历史" : "不带"}</td>
                <td className="text-xs">{String(r.question)}</td>
                <td className={cn("text-xs", !r.ok && "text-amber-600 dark:text-amber-400")}>{r.ok ? "对" : "错"}（{String(r.kind)}）</td>
                <td className="px-3 text-xs tabular-nums">{fmtUsd(num(r.cost_usd))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const MODE_TEXT: Record<string, string> = { bm25: "BM25", vector: "向量", hybrid: "混合（RRF）", hybrid_rerank: "混合 + 重排" };

/** 合同场景检索评测：条款原文 → 找合法依据条文，R@5。 */
export function ContractRetrievalView({ d }: { d: RunDetail }) {
  const s = d.summary;
  const modes = (get(s, "modes") ?? {}) as Record<string, { recall_at_k: number; latency_ms: { p50: number } }>;
  return (
    <div className="space-y-3">
      <p className="text-muted-foreground text-sm">
        后端 {String(s.backend ?? "numpy")} · {num(s.n)} 条查询（金标准风险条款正文 → 找该风险的合法依据条文）。条款语言和法条语言差距大，端到端靠 agent 改写查询（ADR 009）。
      </p>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {Object.entries(modes).map(([m, v]) => (
          <Metric key={m} label={`${MODE_TEXT[m] ?? m} R@${num(s.k)}`} value={pct(v.recall_at_k)} hint={`P50 ${fmtMs(v.latency_ms.p50)}`} />
        ))}
      </div>
    </div>
  );
}

const LONG_MODES: [string, string][] = [
  ["full", "全量历史"],
  ["window", "只留最近 2 轮"],
  ["compress", "摘要压缩"],
];

/** 长对话记忆运行：三种记忆方式的追问正确率（分题型）与输入 token、成本。 */
export function ReviewLongDialogView({ d, onOpen }: { d: RunDetail; onOpen: (traceId: string) => void }) {
  const s = d.summary;
  const modes = LONG_MODES.filter(([m]) => s[m]);
  const cell = (m: string, k: string) => get(s, `${m}.${k}`);
  return (
    <div className="space-y-4">
      <p className="text-muted-foreground text-sm">
        {num(s.dialogs)} 组长对话 × 8 轮（第 7、8 轮回指首次审查和中途决定）；压缩预算 {num(s.budget_chars)} 字，保留最近 {num(s.keep_last)} 轮原文（ADR 011）。
      </p>
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-muted-foreground bg-muted/40 border-b text-left text-xs">
              <th className="px-3 py-2 font-medium">记忆方式</th>
              <th className="font-medium">追问正确率</th>
              <th className="font-medium">回指早期轮次</th>
              <th className="font-medium">近期指代 / 直接问 / 换视角</th>
              <th className="font-medium">平均输入 token</th>
              <th className="font-medium">平均历史字数</th>
              <th className="font-medium">压缩次数</th>
              <th className="px-3 font-medium">花费（含摘要）</th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {modes.map(([m, label]) => (
              <tr key={m} className="border-b last:border-0">
                <td className="px-3 py-1.5">{label}</td>
                <td>{pct(cell(m, "ok"))}</td>
                <td>{pct(cell(m, "early_ok"))}</td>
                <td className="text-xs">{pct(cell(m, "recent_ok"))} / {pct(cell(m, "direct_ok"))} / {pct(cell(m, "flip_ok"))}</td>
                <td>{Math.round(num(cell(m, "input_tokens_mean"))).toLocaleString()}</td>
                <td>{Math.round(num(cell(m, "history_chars_mean"))).toLocaleString()}</td>
                <td>{num(cell(m, "compressions"))}</td>
                <td className="px-3">{fmtUsd(num(cell(m, "total_cost_usd")))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-muted-foreground bg-muted/40 border-b text-left text-xs">
              <th className="px-3 py-2 font-medium">合同</th>
              <th className="font-medium">方式</th>
              <th className="font-medium">轮</th>
              <th className="font-medium">追问</th>
              <th className="px-3 font-medium">结果</th>
            </tr>
          </thead>
          <tbody>
            {d.rows.map((r, i) => (
              <tr key={i} onClick={() => onOpen(String(r.trace_id))} className="hover:bg-accent/50 cursor-pointer border-b last:border-0">
                <td className="px-3 py-1.5 font-mono text-xs">{String(r.contract_id)}</td>
                <td className="text-xs">{LONG_MODES.find(([m]) => m === r.mode)?.[1] ?? String(r.mode)}</td>
                <td className="text-xs">{num(r.turn)}</td>
                <td className="text-xs">{String(r.question)}</td>
                <td className={cn("px-3 text-xs", r.ok === false && "text-amber-600 dark:text-amber-400")}>
                  {r.ok === null ? "—" : r.ok ? "对" : "错"}（{String(r.kind)}）
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

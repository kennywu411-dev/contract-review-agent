import { cn } from "@/lib/utils";

/** 指标卡：大数字 + 说明；有前后对比时显示变化。 */
export function Metric({ label, value, before, hint, good }: { label: string; value: string; before?: string; hint?: string; good?: boolean }) {
  return (
    <div className="bg-card rounded-lg border p-3">
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className={cn("mt-1 text-2xl font-semibold tabular-nums", good === false && "text-amber-600 dark:text-amber-400")}>
        {value}
      </p>
      {(before || hint) && (
        <p className="text-muted-foreground mt-0.5 text-xs">
          {before && <span>校验前 {before}</span>}
          {before && hint && " · "}
          {hint}
        </p>
      )}
    </div>
  );
}

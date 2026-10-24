"use client";

import { BarChart, ScatterChart } from "echarts/charts";
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";

import { CHART_THEME, useIsDark } from "@/lib/theme";

echarts.use([BarChart, ScatterChart, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent, CanvasRenderer]);

type Theme = (typeof CHART_THEME)["dark"];

/** option 用函数生成，拿到当前主题色；切换深浅色时自动重绘。 */
export function EChart({ option, height = 260, label }: { option: (t: Theme) => echarts.EChartsCoreOption; height?: number; label: string }) {
  const el = useRef<HTMLDivElement>(null);
  const dark = useIsDark();
  useEffect(() => {
    if (!el.current) return;
    const chart = echarts.init(el.current);
    const t = CHART_THEME[dark ? "dark" : "light"];
    chart.setOption({
      textStyle: { color: t.text, fontFamily: "inherit" },
      tooltip: {
        backgroundColor: t.tooltipBg,
        borderColor: t.tooltipBorder,
        textStyle: { color: t.tooltipText },
      },
      ...option(t),
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [option, dark]);
  return <div ref={el} role="img" aria-label={label} style={{ height }} className="w-full" />;
}

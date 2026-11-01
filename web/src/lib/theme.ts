"use client";

import { useEffect, useState } from "react";

// ECharts 画在 canvas 上，不认 oklch，这里给出与 globals.css 主题对应的十六进制色
export const CHART_THEME = {
  dark: {
    accent: "#5ccfa0",
    series: ["#5ccfa0", "#8e939c", "#3f9f7a", "#5b6069", "#a8e3c8"],
    text: "#a3a8b1",
    axis: "#2c2f36",
    split: "#23262c",
    tooltipBg: "#1d2025",
    tooltipBorder: "#2f333a",
    tooltipText: "#e6e8eb",
  },
  light: {
    accent: "#1f8f66",
    series: ["#1f8f66", "#6b707a", "#5fb592", "#a6aab2", "#126b4b"],
    text: "#646a74",
    axis: "#d9dbe0",
    split: "#ebedf0",
    tooltipBg: "#fbfbfc",
    tooltipBorder: "#e3e5e9",
    tooltipText: "#22252b",
  },
};

export function useIsDark(): boolean {
  const [dark, setDark] = useState(true);
  useEffect(() => {
    const el = document.documentElement;
    const sync = () => setDark(el.classList.contains("dark"));
    sync();
    const obs = new MutationObserver(sync);
    obs.observe(el, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);
  return dark;
}

export function toggleTheme() {
  const el = document.documentElement;
  const next = !el.classList.contains("dark");
  el.classList.toggle("dark", next);
  try {
    localStorage.setItem("dp-theme", next ? "dark" : "light");
  } catch {}
}

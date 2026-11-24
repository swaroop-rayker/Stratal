// Live chart (DESIGN-SYSTEM c): multi-series lines in the fixed resource colours, built with
// ECharts on the design-system tokens; the PyTorch view is dashed beside the NVML view
// (MON-2.4). Re-reads the tokens when the theme changes.
import { LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
// The ES-module build: the CommonJS one (lib/) arrives as a wrapper object under Vite.
import ReactEChartsCore from "echarts-for-react/esm/core";
import { useEffect, useState } from "react";

echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer]);

export interface Series {
  name: string;
  token: string; // a CSS variable name, e.g. "--res-cpu"
  points: [number, number | null][]; // [time ms, value]
  dashed?: boolean;
  axis?: 0 | 1;
}

function tokens(names: string[]): Record<string, string> {
  const style = getComputedStyle(document.documentElement);
  return Object.fromEntries(names.map((n) => [n, style.getPropertyValue(n).trim()]));
}

export function useTheme(): string | null {
  const [theme, setTheme] = useState(() => document.documentElement.getAttribute("data-theme"));
  useEffect(() => {
    const observer = new MutationObserver(() => {
      setTheme(document.documentElement.getAttribute("data-theme"));
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      observer.disconnect();
    };
  }, []);
  return theme;
}

export function LiveChart({ series, units, label }: { series: Series[]; units: [string, string?]; label: string }) {
  const theme = useTheme();
  const t = tokens(["--border", "--text-muted", "--surface-raised", "--text-primary", "--font-mono", ...series.map((s) => s.token)]);
  const axisLabel = { color: t["--text-muted"], fontFamily: t["--font-mono"], fontSize: 10 };
  const option = {
    animation: false,
    grid: { left: 36, right: units[1] ? 36 : 8, top: 8, bottom: 20 },
    tooltip: {
      trigger: "axis",
      backgroundColor: t["--surface-raised"],
      borderColor: t["--border"],
      textStyle: { color: t["--text-primary"], fontFamily: t["--font-mono"], fontSize: 11 },
    },
    xAxis: { type: "time", splitNumber: 4, axisLabel: { ...axisLabel, hideOverlap: true }, axisLine: { lineStyle: { color: t["--border"] } }, splitLine: { show: false } },
    yAxis: [units[0], units[1]].filter(Boolean).map((u) => ({
      type: "value",
      name: u,
      nameTextStyle: axisLabel,
      axisLabel,
      splitLine: { lineStyle: { color: t["--border"] } },
    })),
    series: series.map((s) => ({
      name: s.name,
      type: "line",
      yAxisIndex: s.axis ?? 0,
      showSymbol: false,
      connectNulls: false,
      data: s.points,
      lineStyle: { color: t[s.token], width: 1.4, type: s.dashed ? "dashed" : "solid" },
      itemStyle: { color: t[s.token] },
    })),
  };
  return (
    <div className="chartbox" role="img" aria-label={label}>
      <div className="legend-s" aria-hidden="true">
        {series.map((s) => (
          <span key={s.name}>
            <i className={s.dashed ? "dash" : undefined} style={{ "--c": `var(${s.token})` } as React.CSSProperties} />
            {s.name}
          </span>
        ))}
      </div>
      <ReactEChartsCore key={theme ?? "dark"} echarts={echarts} option={option} notMerge className="echart" style={{ height: 120 }} />
    </div>
  );
}

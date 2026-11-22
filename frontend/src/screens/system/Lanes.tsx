// SCR-13's resource lanes (approved hi-fi, frames A and C): stacked lanes on one axis in the
// fixed resource colours, thresholds as labelled dashed lines in text-muted, one crosshair
// whose tooltip reads every lane. One ECharts canvas with a grid per lane; the labels and
// the jobs strip are HTML beside and above it, on the same width.
import { LineChart } from "echarts/charts";
import { AxisPointerComponent, GridComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import ReactEChartsCore from "echarts-for-react/esm/core";

import { useTheme } from "../../components/LiveChart";

echarts.use([LineChart, GridComponent, TooltipComponent, MarkLineComponent, AxisPointerComponent, CanvasRenderer]);

export const LANE_H = 56;
const GAP = 8;
const AXIS_H = 20;

export interface Threshold {
  value: number;
  label: string;
}

export interface Lane {
  key: string;
  token: string; // "--res-cpu" …
  label: string;
  sub: string;
  info?: string; // the GPU lane's NVML tooltip (MON-2's note)
  min: number;
  max: number;
  unit: string;
  digits: number;
  thresholds?: Threshold[];
  /** One series, or A and B (B dashed). Points are [x, value]. */
  series: { name: string; points: [number, number | null][]; dashed?: boolean }[];
}

function tokens(names: string[]): Record<string, string> {
  const style = getComputedStyle(document.documentElement);
  return Object.fromEntries(names.map((n) => [n, style.getPropertyValue(n).trim()]));
}

function nearest(points: [number, number | null][], x: number): number | null {
  let best: [number, number | null] | null = null;
  for (const p of points) {
    if (best === null || Math.abs(p[0] - x) < Math.abs(best[0] - x)) best = p;
  }
  return best?.[1] ?? null;
}

/** The lanes' canvas. ``x`` is "time" (ms) or "value" (seconds or epochs). */
export function LanesChart({ lanes, x, min, max, xLabel, tipTitle }: {
  lanes: Lane[];
  x: "time" | "value";
  min: number;
  max: number;
  xLabel: (value: number) => string;
  tipTitle: (value: number) => string;
}) {
  useTheme();
  const t = tokens(["--border", "--text-muted", "--text-secondary", "--surface", "--surface-raised", "--text-primary", "--font-mono", "--border-strong", ...lanes.map((l) => l.token)]);
  const mono = { color: t["--text-secondary"], fontFamily: t["--font-mono"], fontSize: 11 };
  const height = lanes.length * (LANE_H + GAP) + AXIS_H;
  const option = {
    animation: false,
    axisPointer: { link: [{ xAxisIndex: "all" }], lineStyle: { color: t["--text-secondary"], width: 1 }, label: { show: false } },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line" },
      backgroundColor: t["--surface-raised"],
      borderColor: t["--border-strong"],
      padding: [6, 10],
      textStyle: { color: t["--text-primary"], fontFamily: t["--font-mono"], fontSize: 11 },
      formatter: (params: { axisValue: number }[] | { axisValue: number }) => {
        const first = Array.isArray(params) ? params[0] : params;
        if (!first) return "";
        const at = first.axisValue;
        const rows = lanes.flatMap((lane) =>
          lane.series.map((s) => {
            const v = nearest(s.points, at);
            const name = lane.series.length > 1 ? `${lane.label} ${s.name}` : lane.label;
            const value = v === null ? "—" : `${v.toFixed(lane.digits)}${lane.unit}`;
            return `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${t[lane.token] ?? ""};margin-right:6px"></span>${name} <b style="float:right;margin-left:16px;font-weight:500">${value}</b>`;
          }),
        );
        return `<b style="font-weight:500">${tipTitle(at)}</b><br/>${rows.join("<br/>")}`;
      },
    },
    grid: lanes.map((_, i) => ({ left: 0, right: 0, top: i * (LANE_H + GAP) + 2, height: LANE_H })),
    xAxis: lanes.map((_, i) => ({
      gridIndex: i,
      type: x,
      min,
      max,
      axisLine: { lineStyle: { color: t["--border"] } },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: i === lanes.length - 1 ? { ...mono, hideOverlap: true, formatter: (v: number) => xLabel(v) } : { show: false },
    })),
    yAxis: lanes.map((lane, i) => ({
      gridIndex: i,
      type: "value",
      min: lane.min,
      max: lane.max,
      show: false,
    })),
    series: lanes.flatMap((lane, i) =>
      lane.series.map((s, j) => ({
        name: `${lane.label} ${s.name}`,
        type: "line",
        xAxisIndex: i,
        yAxisIndex: i,
        showSymbol: false,
        connectNulls: false,
        data: s.points,
        lineStyle: { color: t[lane.token], width: 1.5, type: s.dashed ? [5, 3] : "solid" },
        itemStyle: { color: t[lane.token] },
        markLine:
          j === 0 && lane.thresholds?.length
            ? {
                silent: true,
                symbol: "none",
                lineStyle: { color: t["--text-muted"], type: [4, 3], width: 1 },
                label: { position: "insideEndTop", ...mono, backgroundColor: t["--surface"], padding: [0, 4] },
                data: lane.thresholds.map((th) => ({ yAxis: th.value, label: { formatter: th.label } })),
              }
            : undefined,
      })),
    ),
  };
  return <ReactEChartsCore echarts={echarts} option={option} notMerge style={{ height, width: "100%" }} />;
}

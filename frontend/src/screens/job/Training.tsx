// SCR-04 v2, the parts of the live job view that are about training (mockup SCR-04v2_v1,
// G2 approved 2026-11-17): the epochs strip with checkpoints (RUN-1.3), the training charts
// (RUN-1.8), the paused and stalled banners (RUN-1.2, RUN-1.6, RUN-1.7) and the device
// benchmark (RUN-1.9). Everything is derived from the job's events.
import { useQuery } from "@tanstack/react-query";
import { type CSSProperties, Fragment, type KeyboardEvent, useEffect, useMemo, useState } from "react";

import { api, call, type Job, type Schemas, type StreamEvent } from "../../api/client";
import type { Feed } from "../../api/stream";
import { Icon } from "../../components/ui";
import { clock, duration } from "../../lib/format";
import { isTraining, runningStage, trainingPosition } from "./jobs";

type Metrics = Record<string, unknown>;

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function metrics(e: StreamEvent): Metrics {
  return e.metrics ?? {};
}

export interface StepPoint {
  step: number;
  loss: number | null;
  accuracy: number | null;
  lr: number | null;
  gradNorm: number | null;
  stepMs: number | null;
  samplesPerS: number | null;
}

export interface EpochPoint {
  epoch: number;
  trainLoss: number | null;
  trainAccuracy: number | null;
  valLoss: number | null;
  valAccuracy: number | null;
}

export interface Checkpoint {
  step: number;
  /** The epoch the checkpoint was written in, or at the end of. */
  epoch: number;
  atEpochEnd: boolean;
}

export interface Pause {
  /** Completed epochs when the job paused: the gap is drawn after this one. */
  afterEpoch: number;
  heat: boolean;
  at: string;
  temperature: number | null;
}

export interface Bench {
  gpu: number | null;
  cpu: number | null;
  choice: "gpu" | "cpu" | null;
  cached: boolean;
}

export interface TrainingData {
  steps: StepPoint[];
  epochs: EpochPoint[];
  checkpoints: Checkpoint[];
  pauses: Pause[];
  bench: Bench | null;
  /** A fine-tune's validation before step 1 (RUN-1.10.AC1): the val curves' starting point,
   * not an epoch done. */
  baseline: EpochPoint | null;
}

const CHECKPOINT_END = /end of epoch (\d+)/;
const CHECKPOINT_IN = /in epoch (\d+), step (\d+)/;

/** Everything SCR-04 v2 shows about a training run, from its events in order. */
export function deriveTraining(events: StreamEvent[]): TrainingData {
  const data: TrainingData = { steps: [], epochs: [], checkpoints: [], pauses: [], bench: null, baseline: null };
  let heatSinceResume: number | null = null;
  let sawHeat = false;
  for (const e of events) {
    const m = metrics(e);
    if (e.type === "action" && m.code === "thermal_pause") {
      sawHeat = true;
      heatSinceResume = num(m.gpu_temp_c);
    }
    if (e.type === "job_state") {
      if (m.to === "paused") {
        data.pauses.push({ afterEpoch: data.epochs.length, heat: sawHeat, at: e.ts, temperature: heatSinceResume });
      }
      if (m.to === "queued") {
        sawHeat = false;
        heatSinceResume = null;
      }
      continue;
    }
    if (e.stage !== "train") continue;
    if (e.type === "stage_progress" && e.substage === "train" && e.step !== null) {
      data.steps.push({
        step: e.step,
        loss: num(m.loss),
        accuracy: num(m.accuracy),
        lr: num(m.learning_rate),
        gradNorm: num(m.grad_norm),
        stepMs: num(m.step_ms),
        samplesPerS: num(m.samples_per_s),
      });
    } else if (e.substage === "validate") {
      const epoch = num(m.epoch);
      const point = epoch === null ? null : { epoch, trainLoss: num(m.train_loss), trainAccuracy: num(m.train_accuracy), valLoss: num(m.val_loss), valAccuracy: num(m.val_accuracy) };
      if (point && epoch === 0) data.baseline = point;
      else if (point) {
        data.epochs = data.epochs.filter((x) => x.epoch !== epoch);
        data.epochs.push(point);
      }
    } else if (e.substage === "checkpoint") {
      const step = num(m.checkpoint_step);
      const end = CHECKPOINT_END.exec(e.message);
      const within = CHECKPOINT_IN.exec(e.message);
      if (step !== null && (end || within)) {
        data.checkpoints.push({ step, epoch: Number((end ?? within)?.[1]), atEpochEnd: end !== null });
      }
    } else if (e.substage === "device") {
      const bench = data.bench ?? { gpu: null, cpu: null, choice: null, cached: false };
      bench.gpu = num(m.device_gpu_step_ms) ?? bench.gpu;
      bench.cpu = num(m.device_cpu_step_ms) ?? bench.cpu;
      if (m.device_choice === "gpu" || m.device_choice === "cpu") bench.choice = m.device_choice;
      if (m.device_benchmark_cached === 1) bench.cached = true;
      data.bench = bench;
    }
  }
  data.steps.sort((a, b) => a.step - b.step);
  data.epochs.sort((a, b) => a.epoch - b.epoch);
  return data;
}

const PAGE = 500;
const MAX_PAGES = 40;

/** The job's whole event history, then its live events; one list, in order, without repeats. */
export function useTrainingEvents(jobId: string, feed: Feed | null, enabled: boolean): StreamEvent[] {
  const history = useQuery({
    queryKey: ["jobs", jobId, "training-events"],
    queryFn: async () => {
      const all: StreamEvent[] = [];
      let cursor: string | null = null;
      for (let page = 0; page < MAX_PAGES; page++) {
        const after: string | null = cursor;
        const result: Schemas["EventList"] = await call(() =>
          api.GET("/api/v1/jobs/{job_id}/events", {
            params: { path: { job_id: jobId }, query: { limit: PAGE, ...(after ? { cursor: after } : {}) } },
          }),
        );
        all.push(...result.items);
        cursor = result.next_cursor;
        if (!cursor) break;
      }
      return all;
    },
    enabled,
  });
  const [live, setLive] = useState<StreamEvent[]>([]);
  useEffect(() => {
    if (!feed) return;
    return feed.on("event", (e: StreamEvent) => {
      setLive((l) => [...l, e]);
    });
  }, [feed]);
  return useMemo(() => {
    const seen = new Set<string>();
    const merged: StreamEvent[] = [];
    for (const e of [...(history.data ?? []), ...live]) {
      if (seen.has(e.event_uid)) continue;
      seen.add(e.event_uid);
      merged.push(e);
    }
    return merged;
  }, [history.data, live]);
}

function configOf(job: Job): { epochs: number | null; lr: number | null; schedule: string | null } {
  const config: Metrics = job.stages[0]?.config ?? {};
  return {
    epochs: num(config.epochs),
    lr: num(config.learning_rate),
    schedule: typeof config.schedule === "string" ? config.schedule : null,
  };
}

/** Region 4: one segment per epoch in job-state colours, checkpoints as diamonds (the kept
 * one filled), and pauses as dashed amber gaps. */
export function Epochs({ job, data }: { job: Job; data: TrainingData }) {
  const { epochs } = configOf(job);
  if (!epochs) return null;
  const stage = runningStage(job);
  const position = trainingPosition(stage);
  const done = data.epochs.length;
  const running = job.state === "running" || job.state === "stalled";
  const finished = job.state === "completed";
  const latest = finished ? null : (data.checkpoints[data.checkpoints.length - 1] ?? null);
  const perEpoch = stage?.total && epochs ? stage.total / epochs : null;
  const items = Array.from({ length: epochs }, (_, i) => i + 1);
  return (
    <>
      <div className="epochs" role="list" aria-label="Epochs">
        {items.map((n) => {
          const state =
            n <= done ? "completed" : n === done + 1 && running ? "running" : n === done + 1 && job.state === "failed" ? "failed" : "pending";
          const checkpointed = data.checkpoints.some((c) => c.epoch === n && c.atEpochEnd) || (latest?.epoch === n);
          const kept = latest !== null && latest.epoch === n;
          const within =
            state === "running" && perEpoch && position.step !== null ? Math.min(99, Math.floor((position.step / perEpoch) * 100)) : null;
          const gaps = data.pauses.filter((p) => p.afterEpoch === n);
          return (
            <Fragment key={n}>
              <div
                className={`ep ${state}`}
                role="listitem"
                aria-label={`Epoch ${String(n)}: ${state}${checkpointed ? (kept ? ", checkpoint kept" : ", checkpoint removed") : ""}`}
              >
                {n}
                {within !== null ? ` · ${String(within)}%` : ""}
                {checkpointed ? <i className={kept ? "ck kept" : "ck"} /> : null}
              </div>
              {gaps.map((g) => (
                <div key={g.at} className="ep gap" role="listitem" aria-label={`Paused ${g.heat ? "for heat" : "by you"} at ${clock(g.at)}`}>
                  {g.heat ? "heat" : "paused"}
                </div>
              ))}
            </Fragment>
          );
        })}
      </div>
      <div className="eplegend" aria-hidden="true">
        <span>
          <i className="ck" />
          checkpoint (removed)
        </span>
        <span>
          <i className="ck kept" />
          checkpoint kept
        </span>
        <span>one every epoch and at least every 5 min</span>
      </div>
    </>
  );
}

// ---------------------------------------------------------------- charts (region 5)

const W = 100;
const H = 58;

function thin<T>(values: T[], max = 200): T[] {
  if (values.length <= max) return values;
  const every = values.length / max;
  return Array.from({ length: max }, (_, i) => values[Math.floor(i * every)] as T);
}

interface Scale {
  x: (v: number) => number;
  y: (v: number) => number;
}

function scale(xs: number[], ys: number[], xMax: number): Scale {
  const lo = Math.min(...ys);
  const hi = Math.max(...ys);
  const pad = hi === lo ? Math.max(Math.abs(hi) * 0.1, 1e-6) : (hi - lo) * 0.1;
  const y0 = lo - pad;
  const y1 = hi + pad;
  const top = Math.max(xMax, ...xs, 1);
  return { x: (v) => (v / top) * W, y: (v) => H - 2 - ((v - y0) / (y1 - y0)) * (H - 4) };
}

function TrainChart({ train, val, xMax }: { train: [number, number][]; val: [number, number][]; xMax: number }) {
  if (train.length + val.length === 0) return <svg className="ch" viewBox={`0 0 ${String(W)} ${String(H)}`} aria-hidden="true" />;
  const s = scale([...train, ...val].map((p) => p[0]), [...train, ...val].map((p) => p[1]), xMax);
  const pts = (ps: [number, number][]) => ps.map(([x, y]) => `${s.x(x).toFixed(1)},${s.y(y).toFixed(1)}`).join(" ");
  return (
    <svg className="ch" viewBox={`0 0 ${String(W)} ${String(H)}`} preserveAspectRatio="none" aria-hidden="true">
      <line className="grid" x1="0" y1={H / 2} x2={W} y2={H / 2} />
      <polyline fill="none" stroke="var(--cat-1)" strokeWidth="1.4" vectorEffect="non-scaling-stroke" points={pts(thin(train))} />
      {val.length ? (
        <>
          <polyline fill="none" stroke="var(--cat-2)" strokeWidth="1.2" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" points={pts(val)} />
          <g fill="var(--cat-2)">
            {val.map(([x, y]) => (
              <circle key={x} cx={s.x(x)} cy={s.y(y)} r="1.8" />
            ))}
          </g>
        </>
      ) : null}
    </svg>
  );
}

function Line({ values, xMax, color = "var(--cat-1)" }: { values: [number, number][]; xMax: number; color?: string }) {
  if (!values.length) return <svg className="ch" viewBox={`0 0 ${String(W)} ${String(H)}`} aria-hidden="true" />;
  const s = scale(values.map((p) => p[0]), values.map((p) => p[1]), xMax);
  return (
    <svg className="ch" viewBox={`0 0 ${String(W)} ${String(H)}`} preserveAspectRatio="none" aria-hidden="true">
      <line className="grid" x1="0" y1={H / 2} x2={W} y2={H / 2} />
      <polyline
        fill="none"
        stroke={color}
        strokeWidth="1.4"
        vectorEffect="non-scaling-stroke"
        points={thin(values).map(([x, y]) => `${s.x(x).toFixed(1)},${s.y(y).toFixed(1)}`).join(" ")}
      />
    </svg>
  );
}

function median(xs: number[]): number | null {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)] ?? null;
}

function quantile(xs: number[], q: number): number | null {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(q * s.length))] ?? null;
}

function series(steps: StepPoint[], key: Exclude<keyof StepPoint, "step">): [number, number][] {
  return steps.flatMap((p): [number, number][] => {
    const value = p[key];
    return value === null ? [] : [[p.step, value]];
  });
}

function Key() {
  return (
    <span className="series">
      <span>
        <i style={{ "--c": "var(--cat-1)" } as CSSProperties} />
        train
      </span>
      <span>
        <i className="dot" style={{ "--c": "var(--cat-2)" } as CSSProperties} />
        val
      </span>
    </span>
  );
}

const fixed = (v: number | null, digits: number) => (v === null ? "—" : v.toFixed(digits));

/** Region 5: loss and accuracy (train line, validation dots), learning rate, gradient norm
 * and step time. Clicking any chart opens the table view with the same numbers. */
export function TrainingCharts({ job, data }: { job: Job; data: TrainingData }) {
  const [table, setTable] = useState(false);
  const { epochs, lr, schedule } = configOf(job);
  const stage = job.stages[0] ?? null;
  const total = stage?.total ?? data.steps[data.steps.length - 1]?.step ?? 1;
  const perEpoch = epochs ? total / epochs : 1;
  const last = data.steps[data.steps.length - 1] ?? null;
  const lastEpoch = data.epochs[data.epochs.length - 1] ?? null;
  const valAt = (key: "valLoss" | "valAccuracy") =>
    (data.baseline ? [data.baseline, ...data.epochs] : data.epochs).flatMap((e) => (e[key] !== null ? [[e.epoch * perEpoch, e[key]] as [number, number]] : []));
  const grads = data.steps.flatMap((p) => (p.gradNorm !== null ? [p.gradNorm] : []));
  const times = data.steps.flatMap((p) => (p.stepMs !== null ? [p.stepMs] : []));
  const toggle = { role: "button", tabIndex: 0, onClick: () => { setTable((t) => !t); }, onKeyDown: (e: KeyboardEvent) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setTable((t) => !t); } }, "aria-expanded": table };
  return (
    <>
      <div className="tcharts">
        <div className="tchart" {...toggle} aria-label={`Loss: train ${fixed(last?.loss ?? null, 3)}, validation ${fixed(lastEpoch?.valLoss ?? null, 3)}. Opens the table view.`}>
          <div className="k">
            Loss<span className="spacer" />
            <Key />
          </div>
          <div className="v">{fixed(last?.loss ?? null, 3)}</div>
          <div className="d">
            {lastEpoch?.valLoss !== null && lastEpoch ? <>val <b>{fixed(lastEpoch.valLoss, 3)}</b> after epoch {lastEpoch.epoch}</> : "no validation yet"}
          </div>
          <TrainChart train={series(data.steps, "loss")} val={valAt("valLoss")} xMax={total} />
        </div>
        <div className="tchart" {...toggle} aria-label={`Accuracy: train ${fixed(last?.accuracy !== null && last ? 100 * last.accuracy : null, 1)}%. Opens the table view.`}>
          <div className="k">
            Accuracy<span className="spacer" />
            <Key />
          </div>
          <div className="v">
            {last?.accuracy !== null && last ? (100 * last.accuracy).toFixed(1) : "—"}
            <small style={{ fontSize: "var(--fs-12)", color: "var(--text-muted)" }}>%</small>
          </div>
          <div className="d">
            {lastEpoch?.valAccuracy !== null && lastEpoch ? <>val <b>{(100 * lastEpoch.valAccuracy).toFixed(1)}%</b> after epoch {lastEpoch.epoch}</> : "no validation yet"}
          </div>
          <TrainChart
            train={series(data.steps, "accuracy").map(([x, y]) => [x, 100 * y])}
            val={valAt("valAccuracy").map(([x, y]) => [x, 100 * y])}
            xMax={total}
          />
        </div>
        <div className="tchart" {...toggle} aria-label="Learning rate. Opens the table view.">
          <div className="k">Learning rate</div>
          <div className="v" style={{ fontSize: "var(--fs-16)" }}>{last?.lr !== null && last ? last.lr.toExponential(1) : "—"}</div>
          <div className="d">{schedule ?? "—"}{lr !== null ? `, from ${lr.toExponential(1)}` : ""}</div>
          <Line values={series(data.steps, "lr")} xMax={total} />
        </div>
        <div className="tchart" {...toggle} aria-label="Gradient norm. Opens the table view.">
          <div className="k">Grad norm</div>
          <div className="v" style={{ fontSize: "var(--fs-16)" }}>{fixed(last?.gradNorm ?? null, 2)}</div>
          <div className="d">median {fixed(median(grads), 2)}</div>
          <Line values={series(data.steps, "gradNorm")} xMax={total} />
        </div>
        <div className="tchart" {...toggle} aria-label="Step time. Opens the table view.">
          <div className="k">Step time</div>
          <div className="v" style={{ fontSize: "var(--fs-16)" }}>{last?.stepMs !== null && last ? `${String(Math.round(last.stepMs))} ms` : "—"}</div>
          <div className="d">p95 {quantile(times, 0.95) !== null ? `${String(Math.round(quantile(times, 0.95) ?? 0))} ms` : "—"}</div>
          <Line values={series(data.steps, "stepMs")} xMax={total} />
        </div>
      </div>
      {table ? (
        <div className="table-wrap">
          <table className="t" aria-label="Training metrics per epoch">
            <thead>
              <tr>
                <th className="r">Epoch</th>
                <th className="r">Train loss</th>
                <th className="r">Val loss</th>
                <th className="r">Train accuracy</th>
                <th className="r">Val accuracy</th>
              </tr>
            </thead>
            <tbody>
              {data.epochs.map((e) => (
                <tr key={e.epoch}>
                  <td className="r">{e.epoch}</td>
                  <td className="r">{fixed(e.trainLoss, 4)}</td>
                  <td className="r">{fixed(e.valLoss, 4)}</td>
                  <td className="r">{e.trainAccuracy !== null ? `${(100 * e.trainAccuracy).toFixed(1)}%` : "—"}</td>
                  <td className="r">{e.valAccuracy !== null ? `${(100 * e.valAccuracy).toFixed(1)}%` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </>
  );
}

// ---------------------------------------------------------------- banners and benchmark

/** Frames B and C: replace the activity line while the job is paused or stalled. */
export function StateBanner({ job, data, now, gpuTemp, limits }: {
  job: Job;
  data: TrainingData;
  now: number;
  gpuTemp: number | null;
  limits: { pause: number | null; resume: number | null };
}) {
  const pause = data.pauses[data.pauses.length - 1] ?? null;
  const training = isTraining(job);
  if (job.state === "paused" && job.pause_reason === "thermal") {
    return (
      <div className="state-banner paused" role="status">
        <Icon name="pause" />
        <div>
          <h2>
            Paused for heat{pause?.temperature !== null && pause ? `: the GPU reached ${String(Math.round(pause.temperature))} °C` : ""}
            {limits.pause !== null ? ` (limit ${String(limits.pause)} °C)` : ""}
          </h2>
          <p>
            It resumes by itself below <b>{limits.resume !== null ? `${String(limits.resume)} °C` : "the limit"}</b>
            {gpuTemp !== null ? <>; it is <b>{Math.round(gpuTemp)} °C</b> now</> : null}. Nothing is lost: the run continues
            from {training ? "the checkpoint" : "its finished work"}.
          </p>
        </div>
      </div>
    );
  }
  if (job.state === "paused") {
    return (
      <div className="state-banner paused" role="status">
        <Icon name="pause" />
        <div>
          <h2>Paused by you{pause ? ` at ${clock(pause.at).slice(0, 5)}` : ""}</h2>
          <p>
            It waits for <b>Resume</b>.{" "}
            {training ? "The checkpoint is kept until the run is resumed or cancelled." : "Its finished work is kept; resuming continues from there."}
          </p>
        </div>
      </div>
    );
  }
  if (job.state === "stalled") {
    const stage = runningStage(job);
    const since = stage?.last_progress_at ? (now - new Date(stage.last_progress_at).getTime()) / 1000 : null;
    const beat = job.heartbeat_at ? (now - new Date(job.heartbeat_at).getTime()) / 1000 : null;
    return (
      <div className="state-banner stalled" role="status">
        <Icon name="hourglass" />
        <div>
          <h2>No progress{since !== null ? ` for ${duration(since)}` : ""}</h2>
          <p>
            The worker is alive{beat !== null ? ` (heartbeat ${String(Math.max(0, Math.round(beat)))} s ago)` : ""}.
            {stage?.activity ? <> Last activity: <b>{stage.activity}</b>.</> : null} It returns to Running by itself when progress resumes.
          </p>
        </div>
      </div>
    );
  }
  return null;
}

/** Frame E: while `device: auto` times the model on each device (RUN-1.9). */
export function BenchPanel({ bench }: { bench: Bench }) {
  const row = (label: string, ms: number | null, measuring: boolean) => (
    <>
      <b>{label}</b>
      <span className="progress" aria-hidden="true">
        <i style={{ "--p": ms !== null ? "100%" : measuring ? "50%" : "0%" } as CSSProperties} />
      </span>
      <span>{ms !== null ? <><b>{ms.toFixed(0)} ms</b>/step</> : measuring ? "measuring…" : "waiting"}</span>
    </>
  );
  return (
    <div className="bench" role="status" aria-label="Device benchmark">
      {row("GPU", bench.gpu, bench.gpu === null)}
      {row("CPU", bench.cpu, bench.gpu !== null && bench.cpu === null)}
    </div>
  );
}

// SCR-04 v2 (M8, mockup SCR-04v2_v1): what the job view derives from a training run's events
// (RUN-1.3, RUN-1.6, RUN-1.8, RUN-1.9), and the epochs strip and banners drawn from it.
import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import type { Job, JobStage, StreamEvent } from "../../api/client";
import { BenchPanel, deriveTraining, Epochs, StateBanner } from "./Training";

let uid = 0;
function event(extra: Partial<StreamEvent>): StreamEvent {
  uid += 1;
  return {
    id: uid, event_uid: `e${String(uid)}`, ts: "2026-11-18T10:00:00Z", job_id: "job_0031", stage: "train",
    substage: null, version_id: null, type: "activity", level: "INFO", step: null, total: null, message: "",
    metrics: null, reason: null, suggested_action: null, attempt: 1, ...extra,
  };
}

const progress = (step: number, loss: number) =>
  event({ type: "stage_progress", substage: "train", step, total: 60, message: `step ${String(step)}`, metrics: { loss, accuracy: 0.5, learning_rate: 0.001, grad_norm: 2, step_ms: 12, samples_per_s: 10000 } });
const validate = (epoch: number) =>
  event({ substage: "validate", message: `Epoch ${String(epoch)}`, metrics: { epoch, train_loss: 1, train_accuracy: 0.6, val_loss: 0.9, val_accuracy: 0.55 + epoch / 100 } });
const checkpoint = (step: number, message: string) => event({ substage: "checkpoint", message, metrics: { checkpoint_step: step, checkpoint_bytes: 3_400_000 } });

const run = [
  event({ substage: "device", message: "Benchmarking devices: 20 steps on each", metrics: { device_benchmark_steps: 20 } }),
  event({ substage: "device", message: "Benchmarked the GPU: 12.0 ms per step", metrics: { device_gpu_step_ms: 12 } }),
  event({ substage: "device", message: "Benchmarked the CPU: 41.0 ms per step", metrics: { device_cpu_step_ms: 41 } }),
  event({ substage: "device", message: "Device auto: the GPU is faster", metrics: { device_choice: "gpu", device_gpu_step_ms: 12, device_cpu_step_ms: 41, device_benchmark_cached: 0 } }),
  progress(10, 2.1), progress(20, 1.8), validate(1), checkpoint(20, "Saving checkpoint at the end of epoch 1 (3.3 MB, 24 ms)"),
  progress(30, 1.5), progress(40, 1.3), validate(2), checkpoint(40, "Saving checkpoint at the end of epoch 2 (3.3 MB, 24 ms)"),
  progress(45, 1.2),
  event({ stage: null, type: "action", level: "WARN", message: "GPU at 84 °C: pausing", metrics: { code: "thermal_pause", gpu_temp_c: 84 } }),
  checkpoint(45, "Saving checkpoint in epoch 3, step 5 (3.3 MB, 22 ms)"),
  event({ stage: null, type: "job_state", ts: "2026-11-18T10:31:00Z", message: "Job running → paused", metrics: { from: "running", to: "paused", actor: "supervisor" } }),
];

function trainJob(state: Job["state"], extra: Partial<Job> = {}): Job {
  const stage: JobStage = {
    idx: 0, name: "train", queue: "gpu", status: state === "paused" ? "paused" : "running", config: { model: { name: "small_cnn" }, epochs: 3 },
    input_version_id: "ds_f8e67211dd94", output_version_id: null, cache_hit: false, step: 45, total: 60, throughput: null,
    eta_seconds: null, activity: "Epoch 3/3 · step 5/20", metrics: { epoch: 3 }, peaks: null, started_at: "2026-11-18T10:00:00Z",
    finished_at: null, error: null,
  };
  return {
    id: "job_0031", kind: "train", state, desired_state: null, attempt: 1, dataset: "cifar10", current_stage: "train",
    output_version_id: null, error: null, summary: null, created_at: "2026-11-18T10:00:00Z", started_at: "2026-11-18T10:00:00Z",
    finished_at: null, stages: [stage], ...extra,
  };
}

describe("SCR-04 v2 training", () => {
  test("test_RUN_1_8_steps_epochs_checkpoints_pauses_and_the_benchmark_come_from_the_events", () => {
    const data = deriveTraining(run);

    expect(data.steps.map((s) => s.step)).toEqual([10, 20, 30, 40, 45]);
    expect(data.steps[0]?.loss).toBe(2.1);
    expect(data.epochs.map((e) => e.epoch)).toEqual([1, 2]);
    expect(data.checkpoints).toEqual([
      { step: 20, epoch: 1, atEpochEnd: true },
      { step: 40, epoch: 2, atEpochEnd: true },
      { step: 45, epoch: 3, atEpochEnd: false },
    ]);
    expect(data.pauses).toEqual([{ afterEpoch: 2, heat: true, at: "2026-11-18T10:31:00Z", temperature: 84 }]);
    expect(data.bench).toEqual({ gpu: 12, cpu: 41, choice: "gpu", cached: false });
  });

  test("test_RUN_1_3_the_epochs_strip_marks_checkpoints_and_the_kept_one", () => {
    render(<Epochs job={trainJob("paused", { pause_reason: "thermal" })} data={deriveTraining(run)} />);

    const items = screen.getAllByRole("listitem").map((i) => i.getAttribute("aria-label"));
    expect(items).toEqual([
      "Epoch 1: completed, checkpoint removed",
      "Epoch 2: completed, checkpoint removed",
      "Paused for heat at 10:31:00",
      "Epoch 3: pending, checkpoint kept",
    ]);
  });

  test("test_RUN_1_6_the_heat_banner_says_the_limits_and_the_temperature_now", () => {
    render(
      <StateBanner job={trainJob("paused", { pause_reason: "thermal" })} data={deriveTraining(run)} now={Date.now()} gpuTemp={79.4} limits={{ pause: 83, resume: 75 }} />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Paused for heat: the GPU reached 84 °C (limit 83 °C)");
    expect(screen.getByRole("status")).toHaveTextContent("It resumes by itself below 75 °C; it is 79 °C now.");
  });

  test("test_RUN_1_9_the_benchmark_panel_shows_the_gpu_measured_while_the_cpu_runs", () => {
    render(<BenchPanel bench={{ gpu: 9, cpu: null, choice: null, cached: false }} />);

    expect(screen.getByRole("status", { name: "Device benchmark" })).toHaveTextContent("GPU9 ms/stepCPUmeasuring…");
  });
});

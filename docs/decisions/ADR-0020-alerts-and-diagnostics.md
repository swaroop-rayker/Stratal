# ADR-0020 — Alerts, bottleneck diagnostics, load and unload events, run comparison

**Status:** Accepted by the owner on 2026-11-22, as proposed, with both recommendations ("Approve as proposed"; proposed 2026-11-21, M11, K3). Serves MON-2.6, MON-2.7, MON-3.2, MON-3.3, MON-4.4 and, if time, MON-4.5 (P2). Follows the owner's kickoff decisions K4 to K7 (MILESTONES.md, M11) and OC-7, OC-12.

## Context

- **MON-2.6** asks for alerts at VRAM > 90 %, RAM > 85 %, a GPU temperature limit and no progress for T seconds. **MON-2.7** asks for each alert to be linked to the action taken (pause, fail, smaller batch; RUN-1.5 to RUN-1.7), shown together in the event log.
- **MON-3.2** asks the dashboard to flag a stage whose observed bottleneck is not the one MON-3's table expects. **MON-3.3** asks for VRAM before and after each model load and unload. **MON-4.4** asks for two runs' resource curves overlaid; **MON-4.5** (P2) for trends across past jobs.
- The owner decided at the kickoff (2026-11-20):
  - **K4:** alerts are evaluated in `monitor`. Temperature pauses, no progress marks the job stalled, and VRAM and RAM only warn. An out-of-memory failure after a VRAM warning is linked to it. An alert fires after 5 s over its threshold, once per crossing, and re-arms 5 points below. With no job running, it is stored without one. An action records the alert that caused it.
  - **K5:** `process_samples` gains the job process's CPU in cores, and `monitor` compares expected with observed over 30 s. Three first rules apply to a GPU stage with the GPU under 30 %. A diagnostic is a WARN event.
  - **K6:** MON-3.3 shows PyTorch's allocated memory, with the card's figure beside it.
  - **K7:** runs are aligned by time since start with pauses removed; two training runs can also be aligned by epoch.
- **OC-7:** 90 % is of the card's total (NVML), and 85 % is of the WSL VM's 10 GB. **OC-12:** "CPU at 100 %" means the job process's own CPU against what it can use.
- **What exists.** `monitor` is one process with three threads (ADR-0005 decision 6): collector, sampler, watchdog.
  - The watchdog already pauses the running GPU job at 83 °C and resumes it below 75 °C (RUN-1.6), reading the newest sample's temperature. It also marks a silent stage `stalled` after 300 s (RUN-1.7). Both write `action` events with a `code`.
  - Out-of-memory failures already carry codes: `gpu_oom` for a training batch (RUN-1.5) and `host_oom_killed` when the kernel kills a stage over its container limit.
  - The VRAM manager (RUN-1.4) already emits load and unload events with PyTorch's allocated and reserved memory and the card's free memory, before and after. It is used by labelling and by the inference service. These events are of type `activity`; the types `model_loaded` and `model_unloaded` are in the event schema but unused.
  - The training stage already reports `loader_workers` in a metric. Training has no load or unload event: its process exits when the stage ends (ADR-0006).
  - The job's reporter thread sends RSS and PyTorch figures as process samples once a second (ADR-0005 decision 5); it does not send CPU.
- **NFR-11:** monitoring stays under 2 % of the machine's CPU and adds under 1 % to a job's runtime. **NFR-13:** a monitor crash does not crash jobs. Both are measured again in M11.

## Decision

### A. Alerts (MON-2.6, MON-2.7)

1. **Four alerts**, each with a stable `code`:

   | Code | Condition | Fires after | Re-arms | Action (MON-2.7) |
   |---|---|---|---|---|
   | `vram_high` | NVML used ÷ total > 90 % | 5 s | below 85 % | none at the time: warns. A later `gpu_oom` failure of the job it was tagged with links to it (rule 5) |
   | `ram_high` | VM (total − available) ÷ total > 85 % | 5 s | below 80 % | none at the time: warns. A later `host_oom_killed` failure links to it |
   | `gpu_hot` | GPU temperature ≥ the pause limit (83 °C, SCR-14) | 5 s | below the resume limit (75 °C, SCR-14; point 1 below) | pause the running GPU job (RUN-1.6) |
   | `no_progress` | a running stage silent for the stall timeout (300 s, SCR-14) | the timeout itself | when progress returns | mark the job `stalled` (RUN-1.7) |

   The thresholds are settings (rule 9). "5 points below" is in percentage points for VRAM and RAM.

2. **Evaluated where the numbers are.**
   - The three resource alerts are evaluated in the **sampler thread**, on each machine sample as it is taken, with no database read.
   - `no_progress` stays in the watchdog's stall check, which already measures silence.
   - The state machine is a pure function in `stratal.mon.alerts`: `step(state, sample, limits) → (state, fired | cleared | None)`. State per code is *armed*, *over since t* or *fired*. An alert fires when the condition has held from the first sample over the threshold to one at least 5 s later. At the idle interval of 10 s, that means two samples in a row.
   - A sample with a figure missing (NVML unavailable) leaves that alert's state unchanged.

3. **Stored as events, nothing new to query.**
   - An alert is an `events` row: `type = "alert"`, level WARN, with the reason and suggested action MON-1.3 requires. Its `metrics` are `{code, state: "fired", value, threshold, rearm_below}`.
   - It carries the job and stage the sample was tagged with (MON-2.3). With no job running, both are null (K4).
   - Re-arming writes an INFO `alert` event, `{code, state: "cleared", value}`, linked to the alert it ends (rule 4). That gives every alert a duration.
   - On start, the monitor reads each code's latest alert event. A firing with no clearing means that code is still *fired*, so a restart does not fire it twice.

4. **The link is a column.** `events.caused_by` is a nullable `uuid`: the `event_uid` of the alert that caused this event (a schema change; DATA_MODEL.md §4.6 and a migration).
   - It is set on an action event and on a clearing.
   - It uses the `event_uid`, not `id`, because whoever writes the action knows the alert's uid before its row exists.
   - A partial index on `(caused_by) where caused_by is not null` serves the reverse lookup: the actions an alert caused.

5. **Actions and their links.**
   - `gpu_hot` calls the watchdog's existing pause (one transaction, as now). The `thermal_pause` action event gets `caused_by`. The watchdog's own heat check keeps only the resume side; the pause now waits for the 5 s hold (point 2 below).
   - `no_progress` writes the alert, then the existing `stalled` action with `caused_by`, in one transaction.
   - For VRAM and RAM there is no action at the time. When `insert_events` (`stratal.mon.store`, the one function every writer uses) stores an event with `code` `gpu_oom` or `host_oom_killed`, it sets `caused_by`. It points to the latest `vram_high` or `ram_high` alert for that job if that alert is still fired or cleared less than 60 s earlier. One indexed lookup, only for those two rare codes.
   - The suggested batch of RUN-1.5 stays in the failure's own event: it is the "smaller batch" action.
   - With no GPU job to pause, the `gpu_hot` alert says so in its message and has no action.

### B. Bottleneck diagnostics (MON-3.2)

6. **The job's CPU.** The job child's reporter thread adds `cpu_cores` to every process sample. That is the CPU time of the child and all its children (DataLoader workers included) since the last sample, divided by the wall time between them, from psutil. It is a new nullable `real` column on `process_samples`, averaged into `process_samples_1m`.
   - **Capacity** is what the job can use: `min(1 + loader_workers, the logical CPUs the container sees)`.
   - `loader_workers` comes from the stage's metric, as training already reports it; a stage that reports none counts as 0.
   - **Saturated** means a mean of at least 85 % of capacity over the window (OC-12).

7. **Expected resource per stage.** `stratal.mon.expect` maps each stage to MON-3's expected bottleneck: ingest disk and CPU; clean, preprocess, tokenize and split CPU; label GPU; train and test_eval GPU on the `gpu` queue and CPU on `cpu`.
   - The rules below apply to stages on the `gpu` queue only (K5). CPU stages show their expected and observed figures without a rule.
   - More rules can join the table later without schema changes.

8. **The window and the three rules.**
   - Every watchdog check (5 s), for each running GPU stage, the watchdog reads the last 30 s of that job's machine and process samples: two small indexed queries.
   - A diagnosis needs at least 20 samples, a stage running for at least 60 s since its start or resume (loading and warm-up are not bottlenecks), and a mean GPU utilisation under 30 %. Then:
     1. **The job's CPU saturated → data loading.** "Data loading is the bottleneck: GPU at 12 % while the job's CPU is at 1.0 of 1 core. Increase DataLoader workers (now 0)."
     2. **Else disk reads at least 50 MB/s → the disk.** "The disk is the bottleneck: reading 180 MB/s with the GPU at 18 %. Preprocess to a smaller size or keep the data in memory." The 50 MB/s is a first guess, a setting; it is checked on the acceptance run and changed if the normal reading is near it.
     3. **Else everything low → small batches or synchronisation.** "The GPU is waiting: GPU at 20 %, the job's CPU at 0.4 of 3 cores, disk idle. Try a larger batch, or look for `.item()` or `.cpu()` calls inside the loop."
   - The function is pure, `diagnose(window, expected) → Diagnosis | None`. The watchdog and the API call the same function (rule 13).
   - **Stored** as an event with a new type `diagnostic` (DATA_MODEL.md's type list): level WARN, the rule's `code` (`diag_data_loading`, `diag_disk`, `diag_gpu_waiting`), the window's means in `metrics`, and the reason and suggested action. It therefore appears in SCR-04's log (K5) and on SCR-13.
   - **Once per crossing:** a rule fires again only after a window in which the GPU was at 30 % or more (an INFO `diagnostic` event, `state: "cleared"`, `caused_by` the diagnosis), and at most once every 10 minutes per stage.

### C. Load and unload events (MON-3.3)

9. **Typed, with both figures.**
   - The VRAM manager's result events take the types `model_loaded` and `model_unloaded`; its "Loading…" and "Unloading…" events stay `activity`.
   - Beside the existing allocated, reserved and free figures, `metrics` gains `card_used_mb_before` and `card_used_mb_after`: total minus free, the driver's count, which includes other processes as NVML's does (K6).
   - The unload message ends with both: "Unloaded Florence-2-base: VRAM 4.1 → 0.3 GB (PyTorch), card 4.6 → 0.8 GB" (MON-3.3.AC1).
   - The VRAM manager is RUN-1.4's 👤 unit, which the owner asked the agent to write (ADR-0012's log, 2026-11-19). The change touches only its two emits, and approving this ADR approves it.

10. **Training too.** The training stage emits `model_loaded` once the network and optimizer are on the GPU (allocated before and after), and `model_unloaded` at the stage's end. Before the process exits, it drops the model and optimizer, empties the cache and measures. That is telemetry around the owner's loop; the loop itself does not change. The inference service already goes through the VRAM manager, so it needs nothing more.

    **Correction (2026-11-22, found while drafting SCR-13's low-fi).** Training already emits load and unload events through the VRAM manager v2 (RUN-1.4, M8; for example `job_0106`: "Loaded small_cnn to GPU", "Unloaded small_cnn …"). So rule 10 needs nothing beyond rule 9's types and card figure. The Context's line saying training has no such event was wrong.

### D. Two runs overlaid (MON-4.4) and history (MON-4.5)

11. **Alignment.**
    - **By time:** x is the seconds a job spent `running`, from its `job_transitions`, so paused and queued time is removed (K7). Samples taken while it was not running are dropped.
    - **By epoch:** available when both jobs are training runs. A sample's x is the epoch it fell in plus the fraction of that epoch done, interpolated between the stage's progress events.
    - Each series is thinned evenly to at most 600 points, as `/jobs/{id}/telemetry` already does, and reads the one-minute table beyond 7 days (NFR-12).
    - The series: GPU utilisation, card VRAM, GPU temperature, machine CPU and RAM from the sampler; the job's RSS, PyTorch allocated memory and `cpu_cores` from its process samples. The machine figures include anything else that ran at the time, and SCR-13 says so.
12. **History (P2, if time).** The last N jobs' peaks and throughput per stage, from `jobs.summary` and `job_stages.peaks` (MON-2.5). No new table.

### E. API, settings, cost

13. **Endpoints** (API.md, tag `monitoring`):
    - `GET /system/alerts?since=&until=&job=`: alerts with their clearing and the actions linked through `caused_by`.
    - `GET /system/diagnostics?since=`: past diagnostics, plus `live`, each running stage's expected and observed figures over the last 30 s from the same `diagnose`.
    - `GET /system/models?since=`: load and unload events.
    - `GET /system/compare?a=&b=&align=time|epoch`: the two series of rule 11.
    - `GET /system/history?n=` (P2).
    - SCR-13's resource history uses a range form of the existing telemetry query: `GET /telemetry?from=&to=`.
    - Live updates come from the existing global SSE channel. The alert, diagnostic and load events are ordinary events.
14. **Settings.** A `monitoring` row in `settings` (DATA_MODEL.md §4.13), shown on SCR-14 (its M11 section):
    - `{sample_interval_active_s: 1, sample_interval_idle_s: 10, vram_alert_percent: 90, ram_alert_percent: 85, disk_bottleneck_mb_s: 50}`;
    - until saved, the environment's values apply, as for `training`;
    - the sampler reads the row each loop, as the watchdog does for `training`;
    - temperature and stall limits stay in `training`, where they already are;
    - the 5 s hold and the 30 s window are constants, not settings.
15. **Cost (NFR-11) and independence (NFR-13).**
    - Alerts add a comparison per sample in memory.
    - Diagnostics add two indexed queries per running GPU stage every 5 s.
    - The reporter adds one psutil call per second in the job. Its CPU-time call covers the child processes, so it is the costliest new part, and NFR-11's rerun measures it.
    - If `monitor` crashes, alerts, pauses for heat and diagnostics stop while jobs continue, as today for the watchdog. SCR-13 then shows the monitor's health, as its empty state does.

### F. Tests

- `test_MON_2_6_*`:
  - the state machine on simulated samples: each of the four fires after its hold, once per crossing, and re-arms below its re-arm point;
  - a missing figure leaves the state unchanged;
  - a restart does not fire twice;
  - integration: one fired alert per code stored with its job, or without one when idle.
- `test_MON_2_7_*`:
  - integration: a simulated hot GPU pauses the job, with `thermal_pause.caused_by` the alert;
  - a `gpu_oom` failure within 60 s of `vram_high` links to it;
  - `/system/alerts` returns each alert with its action.
  - The SCR-04 log shows them together (Vitest).
- `test_MON_3_2_*`:
  - `diagnose` on simulated windows: each of the three rules, nothing during warm-up, nothing with the GPU at 30 % or more, once per crossing;
  - integration: a slow-loader job (a dataset whose `__getitem__` sleeps, with no loader workers) on a GPU-queue stage gets `diag_data_loading` (MON-3.2.AC1). It runs on CPU torch, so no GPU is needed; the samples are planted.
- `test_MON_3_3_*`: a fake GPU backend's unload event has the type `model_unloaded`, both figures and the "VRAM a → b GB" message; the same on the GPU (marker `gpu`).
- `test_MON_4_4_*`: alignment by time with a pause removed, alignment by epoch, the series thinned; the overlay renders (Vitest).
- `test_MON_4_5_*` (P2, if time).

## As built in M11 (2026-11-22)

- **The inference service's events reach the log.** Its holder used a log-only sink, so its loads and unloads never reached `events`; rule 9's claim that it "needs nothing more" was wrong. It now has a `StreamSink` (`stratal.mon.sink`), which sends each event to the stream with no job, and to the log as before. No spool file: the service has no job folder.
- **The sink protocol gains `event_type`** (`activity`, `model_loaded`, `model_unloaded`), so the VRAM manager can type its two result events without a second channel. `JobContext`, `LogSink`, `StreamSink` and the test sink take it.
- **A stall alert also clears when the supervisor ends the stalled stage**, not only when the watchdog sees progress return: the watchdog clears any open `no_progress` alert whose job is no longer `stalled` (found by the integration test).
- **An unknown stage on the GPU queue is expected to be GPU-bound** in `stratal.mon.diagnose.expected`, as the synthetic test stage is.
- **`GET /telemetry` also returns the jobs in the range with their stages**, so SCR-13's jobs strip needs one request, not a second one to `GET /jobs`.
- **Alert limits are read at most every 5 s and sampling intervals every 30 s** (`_AlertLimits` in `stratal.mon.__main__`), so the sampler adds no query per sample (NFR-11).

## Alternatives

| Option | Why not |
|---|---|
| An `alerts` table with open and close times | A second record of what the events already hold; MON-2.7 wants the alert in the event log anyway. Events plus `caused_by` give durations and links with one column |
| Linking actions to alerts by time when read | No schema change, but a guess. K4 asks for the cause to be recorded |
| Evaluating alerts in the watchdog from stored samples | A database read every check, and up to 10 s late. The sampler has the figures in hand |
| Diagnostics in the job process | Ties the training loop to monitoring (SPEC MON-2: the loop knows nothing about it) and dies with the job |
| Machine-wide CPU for MON-3.2 | On 16 logical CPUs a saturated loader core is about 6 % of the machine (OC-12, rejected at the kickoff) |
| NVML inside the job for the card's figure | A second NVML client per job. `cudaMemGetInfo` is already called and gives the driver's count |

## Consequences

- Two schema changes and a migration: `events.caused_by` and `process_samples.cpu_cores` (with `process_samples_1m.cpu_cores`), plus the event type `diagnostic`. All are nullable or additive, so older rows stay valid.
- RUN-1.6's pause waits 5 s longer than today (point 2). Its tests change to feed held samples.
- The VRAM manager's events change type. SCR-04 shows them as before; filters by type gain two values.
- A diagnosis is a hint in words, not a measurement of the cause. The disk threshold is a guess until the acceptance run.
- SCR-13 (G2 next) has concrete sources for each region. SCR-04's and SCR-14's addenda are checked at G3 (K11).

## Points settled by the owner (2026-11-22)

1. **`gpu_hot` re-arms at the resume limit (75 °C), not 5 °C below the pause limit (78 °C): accepted.** The job stays paused until 75 °C anyway, so at 78 °C a second crossing would fire an alert about a job that is already paused.
2. **The thermal pause waits for the 5 s hold, like every alert: accepted.** Today it pauses on one reading at 83 °C. Five seconds at 83 °C does no harm, and one noisy reading no longer pauses a run.

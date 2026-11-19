# Stratal

A local, end-to-end ML lifecycle platform that runs on consumer hardware. It turns raw data into versioned datasets, trains and fine-tunes models, evaluates them, and serves them through a chat UI and an engineer dashboard.

**Status:** milestone M0 (environment and skeleton). The stack starts, the API answers a health check, and the GPU is usable from containers. No pipeline features exist yet; ingestion is milestone M1. Progress is tracked in [docs/MILESTONES.md](docs/MILESTONES.md).

## Target machine

One laptop: RTX 4050 (6 GB VRAM), about 15 GB of RAM, Windows 11 with Docker Desktop on WSL2. Everything is designed to fit inside that: under 5.5 GB of VRAM per job and under 9 GB of RAM in total.

## Setup

You need Docker Desktop (WSL2 backend) and git. Python runs only inside containers.

1. Cap WSL's memory. Create `%USERPROFILE%\.wslconfig`, then run `wsl --shutdown` and start Docker Desktop again:

   ```ini
   [wsl2]
   memory=10GB
   swap=4GB
   ```

2. If your antivirus or network inspects HTTPS, put its root certificate in `docker/certs/` as a `.crt` file. See [docker/certs/README.md](docker/certs/README.md). Skip this otherwise.

3. Build and start:

   ```bash
   docker build --target base -t stratal-base -f docker/Dockerfile .
   ```

   ```bash
   docker compose --profile dev --profile gpu build
   ```

   ```bash
   docker compose up -d
   ```

4. Check it: open `http://127.0.0.1:8000/api/v1/health`. It returns `{"status":"ok", ...}` when postgres, redis and the data volume are reachable.

## Everyday commands

| Purpose | Command |
|---|---|
| Start / stop the stack | `docker compose up -d` / `docker compose down` |
| All quality gates | `.\scripts\check.ps1` |
| Quality gates without integration tests | `.\scripts\check.ps1 -Fast` |
| Check the GPU from a container | `docker compose run --rm worker-gpu python scripts/spikes/s01_gpu_check.py` |
| Quality gates plus GPU tests | `.\scripts\check.ps1 -Gpu` |
| Quality gates plus the timing benchmarks (NFR-11) | `.\scripts\check.ps1 -Bench` |
| Phase-1 acceptance run on the live stack | `docker compose run --rm dev pytest -q -s -m acceptance tests/acceptance` |
| Store a job's events the database missed | `docker compose run --rm dev python -m stratal.cli events backfill --job job_0042` (or `--all`) |
| Logs | `docker compose logs -f api` (or `worker-cpu`, `monitor`, `beat`) |
| Apply database migrations | `docker compose run --rm dev alembic upgrade head` |
| Start a pipeline job | `POST http://127.0.0.1:8000/api/v1/jobs` (see docs/API.md §3.3); sources go under `.\sources` |
| Review a sample of a version | `POST http://127.0.0.1:8000/api/v1/versions/{id}/reviews` with `{"sample_size": 50}`, then `PUT …/reviews/{rev}/items/{n}` and `POST …/reviews/{rev}/close` (docs/API.md §3.5) |
| Build or read a report | `POST http://127.0.0.1:8000/api/v1/versions/{id}/reports`; `GET …/versions/{id}/reports/latest` (docs/API.md §3.6) |
| Open the web UI | http://127.0.0.1:5173 (the `frontend` service, started with the stack) |
| Frontend checks (also in `check.ps1`) | `docker compose run --rm frontend-tools sh -c "npm run typecheck && npm run lint && npm test && npm run build"` |
| Regenerate the frontend's API types after an API change | `docker compose run --rm frontend-tools npm run api:types` |
| Keep Windows awake while jobs run | `.\host\stratal-host.ps1` |

## Benchmarks (NFR-3)

Measured on 2026-11-15 on the RTX 4050 Laptop GPU (6 GB) in the M4 demo (`job_0006`): 1,000 COCO 2017 images through the whole phase-1 pipeline as one job, in about 2 minutes 20 seconds. Florence-2-base with `<CAPTION>`, fp16, three beams, batch 16; CLIP ViT-B/32.

| Stage | Throughput | Notes |
|---|---|---|
| ingest | 91 files/s | reads through the Windows mount (spike S-04) |
| clean | 202 images/s | pHash, exact and near duplicates |
| preprocess | 139 images/s | COCO images are already ≤ 640 px, so nothing was resized |
| label | 8.9 images/s overall | captioning about 10 images/s, CLIP scoring about 100 pairs/s; model loads 1.5 s and 0.6 s |
| filter | under 0.1 s | with schema validation (DATA-5.1), 0.28 s on 994 rows (M5, `job_0008`) |
| split | 0.17 s for 985 rows | including the all-pairs leakage check over 148,683 cross-split pairs (M5, `job_0010`) |
| evaluate | 0.03 s | the phase-1 report (M5, `job_0010`) |

- **NFR-3 (captioning ≥ 2 images/s):** met five times over; no change to the target is needed.
- **Peak VRAM while labelling:** 4.20 GB in use on the card (NVML, the Windows desktop's 0.22 GB included); PyTorch allocated at most 3.55 GB. NFR-1's limit is 5.5 GB.
- **Peak RAM of the label process:** 2.5 GB, under the GPU worker's 4.5 GB container limit.
- **At 2,000 images** (spike S-07, `job_0007`): 4 minutes 56 seconds end to end, labelling at 8.7 images/s; every container under half its memory limit.
- **At 10,000 images** (M5, `job_0013`): 17 minutes end to end. 2,983 images came from the cache (they are also in coco1k and coco2k); the other 6,949 were captioned at 8.7 images/s, with no out-of-memory retry at batch 16. The split took 5.6 s, including its leakage check over 16.5 million cross-split pairs.
- Spike S-06 has the same captioners at batch sizes 4, 8 and 16 ([docs/RISKS.md](docs/RISKS.md)).

### Phase-1 acceptance (M7, 2026-11-17)

`job_0026` ran **20,000** COCO 2017 images (`coco20k`: coco10k plus 10,000 images never captioned) through all seven stages in **27.4 minutes**, measured by `tests/acceptance` from the stack's own telemetry:

| Stage | Minutes | Notes |
|---|---|---|
| ingest | 4.37 | 76 files/s; no per-item cache, so a crash here restarts it (NFR-5 bound: 10 minutes) |
| clean | 1.00 | 19,873 rows hashed or read from the pHash cache |
| preprocess | 1.54 | |
| label | 19.86 | **9,917 new captions, 8.3 images/s** over the whole stage (model loads and CLIP scoring included); 9,926 from the cache; batch 16 throughout |
| filter, split, evaluate | 0.48 together | 19,644 rows kept; split `ds_005e3e700ee8`; report 13 |

- **NFR-2 (RAM under 9 GB):** the WSL VM's used memory peaked at **3.9 GB**.
- **NFR-1 (VRAM under 5.5 GB):** the job's PyTorch reserve peaked at **3.87 GB**; the card's total at 4.20 GB.
- **NFR-3 (captioning ≥ 2 images/s):** 8.3 images/s on uncached images, four times the target.
- **NFR-5 (no more than 10 minutes lost to a crash):** clean, preprocess and label resume from per-batch caches (the kill tests, `test_NFR_5_*`); the stages without one take at most 4.4 minutes on 20,000 images.
- **coco10k again** (`job_0025`): the same versions as M5's `job_0013`, from the ingest version to the split, in 2 minutes 21 seconds (ingest reread every file; every later stage was a memoized hit).

### Monitoring overhead (NFR-11, M7)

`check.ps1 -Bench`: a CPU-bound synthetic stage with one event per item (about 50 events a second, far above a real stage), three runs with monitoring off and three on. On a quiet machine: runtime **+0.02 %**; the `monitor` process used **1.58 % of the machine's 16 logical CPUs, which is 25 % of one core**, mostly the collector storing and publishing each event. That is within NFR-11, which the owner reads as a share of the machine (SPEC OC-6).

### Training demo (M8, 2026-11-19)

`job_0051` trained the reference CNN (`small_cnn`, 0.29 M parameters) on CIFAR-10 (`ds_f8e67211dd94`) on the GPU for 30 epochs: **84.3 %** validation accuracy in 108 s of training.

- **Pause and resume from SCR-07:** the pause saved a checkpoint at global step 3,244 (epoch 9, step 260; 3.3 MB in 124 ms), and the resumed attempt started at step 3,244. While paused, SCR-11 showed the run as `partial`, with its weights in the checkpoint; once complete, it was `trained` and the lineage's latest version, `mv_359e979462ab`. Each epoch's checkpoint took about 28 ms.
- **NFR-1:** PyTorch's peak reserve was **420 MB**. `test_NFR_1_*` measures 0.79 GB for the same CNN at batch 512.
- **Retention:** run automatically after the run completed (`job_0052`). Under `keep_intermediate = 0` it deleted the weights of `job_0030` and `job_0031` (2.3 MB, as the dry run predicted) and kept the base, the latest and the pinned `mv_e80834ce01b0`; the deleted versions keep their metrics. The disk report matches the bytes on disk with no orphans.
- **Prediction:** SCR-11's try-out answered in 146 ms, on the CPU until RUN-1.4 is written. A deleted version answers `409 weights_deleted`.

## Documentation

| | |
|---|---|
| [docs/KICKOFF.md](docs/KICKOFF.md) | What was agreed and what happens next |
| [docs/SPEC.md](docs/SPEC.md) | Requirements |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it is built |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Schema and invariants |
| [docs/API.md](docs/API.md) | Interfaces |
| [docs/MILESTONES.md](docs/MILESTONES.md) | Plan and current status |
| [docs/RISKS.md](docs/RISKS.md) | Risks and spike results |
| [docs/decisions/](docs/decisions/) | Architecture decision records |

## Phases

1. Data operations, image→text auto-labeling, evaluation
2. Training runtime, model builder, classical ML, hyperparameter search
3. LLM: caption refinement, RAG, router, tools
4. Text→image LoRA fine-tuning
5. Translation, full chat UI, dashboard polish

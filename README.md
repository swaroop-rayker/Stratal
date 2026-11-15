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
| Logs | `docker compose logs -f api` (or `worker-cpu`, `monitor`) |
| Apply database migrations | `docker compose run --rm dev alembic upgrade head` |
| Start a pipeline job | `POST http://127.0.0.1:8000/api/v1/jobs` (see docs/API.md §3.3); sources go under `.\sources` |
| Keep Windows awake while jobs run | `.\host\stratal-host.ps1` |

## Benchmarks (NFR-3)

Measured on 2026-11-15 on the RTX 4050 Laptop GPU (6 GB) in the M4 demo (`job_0006`): 1,000 COCO 2017 images through the whole phase-1 pipeline as one job, in about 2 minutes 20 seconds. Florence-2-base with `<CAPTION>`, fp16, three beams, batch 16; CLIP ViT-B/32.

| Stage | Throughput | Notes |
|---|---|---|
| ingest | 91 files/s | reads through the Windows mount (spike S-04) |
| clean | 202 images/s | pHash, exact and near duplicates |
| preprocess | 139 images/s | COCO images are already ≤ 640 px, so nothing was resized |
| label | 8.9 images/s overall | captioning about 10 images/s, CLIP scoring about 100 pairs/s; model loads 1.5 s and 0.6 s |
| filter | under 0.1 s | |

- **NFR-3 (captioning ≥ 2 images/s):** met five times over; no change to the target is needed.
- **Peak VRAM while labelling:** 4.20 GB in use on the card (NVML, the Windows desktop's 0.22 GB included); PyTorch allocated at most 3.55 GB. NFR-1's limit is 5.5 GB.
- **Peak RAM of the label process:** 2.5 GB, under the GPU worker's 4.5 GB container limit.
- **At 2,000 images** (spike S-07, `job_0007`): 4 minutes 56 seconds end to end, labelling at 8.7 images/s; every container under half its memory limit.
- Spike S-06 has the same captioners at batch sizes 4, 8 and 16 ([docs/RISKS.md](docs/RISKS.md)).

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

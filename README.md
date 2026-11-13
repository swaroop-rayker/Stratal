# Stratal

A local, end-to-end ML lifecycle platform that runs on consumer hardware. It turns raw data into versioned datasets, trains and fine-tunes models, evaluates them, and serves them through a chat UI and an engineer dashboard.

**Status:** kickoff. The documentation set is written and approved. No code exists yet, so there is nothing to install or run. Setup and run instructions are added in milestone M0, when there is something to run.

## Target machine

One laptop: RTX 4050 (6 GB VRAM), about 15 GB of RAM, Windows 11 with Docker Compose on WSL2. Everything is designed to fit inside that: under 5.5 GB of VRAM per job and under 9 GB of RAM in total.

## Documentation

| | |
|---|---|
| [docs/KICKOFF.md](docs/KICKOFF.md) | What was agreed and what happens next |
| [docs/SPEC.md](docs/SPEC.md) | Requirements |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it is built |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Schema and invariants |
| [docs/API.md](docs/API.md) | Interfaces |
| [docs/MILESTONES.md](docs/MILESTONES.md) | Plan and current status |
| [docs/RISKS.md](docs/RISKS.md) | Risks and spikes |
| [docs/decisions/](docs/decisions/) | Architecture decision records |

## Phases

1. Data operations, image→text auto-labeling, evaluation
2. Training runtime, model builder, classical ML, hyperparameter search
3. LLM: caption refinement, RAG, router, tools
4. Text→image LoRA fine-tuning
5. Translation, full chat UI, dashboard polish

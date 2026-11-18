# Spike S-08: training the reference models, GPU against CPU

**Run:** 2026-11-18, M8, by the agent. **Script:** `scripts/spikes/s08_training.py`. **Raw results:** `S-08-results.json`.
**Data:** CIFAR-10's balanced split `ds_f8e67211dd94` (47,704 train, 5,963 val; 32 × 32 px; DECISIONS.md, M8).
**Machine:** RTX 4050 Laptop (6 GB), the `worker-gpu` container (4,500 MB limit, 16 logical CPUs visible, PyTorch using 8 threads), PyTorch 2.15 with CUDA 13.2, VRAM cap 0.85.

ADR-0014 assumed four things this spike checks: that the input pipeline can stream without a tensor cache, how each device compares (RUN-1.9), that checkpoints are cheap enough to write every epoch and every 5 minutes, and that training fits the memory budget (ADR-0007).

## 1. Input pipeline

Images per second read from the blob store and decoded, batch 256, first 40 batches:

| Loader workers | Images/s |
|---|---|
| 0 (in the training process) | 1,570 |
| 2 | 7,826 |
| 4 | 12,691 |

Decoding the whole train split into one tensor instead took 22.6 s in one process and 575 MB of RAM.

**Finding.** With 4 workers the loader delivers more than the GPU consumes (the small CNN trains at about 11,000 images/s, below). Decoding is not the bottleneck, so no tensor cache is built (ADR-0014 rule 4 stands). With 2 workers it would be, by about a third.

## 2. Training steps

Median of 20 timed steps after 5 warm-up steps, Adam, data already in memory, so only the model is timed:

| Model | Parameters | Device | Batch | ms/step | Samples/s | Peak VRAM allocated / reserved |
|---|---|---|---|---|---|---|
| `mlp` | 820,874 | GPU | 128 | 3.6 | 35,952 | 22 / 42 MB |
| `mlp` | | GPU | 256 | 4.1 | 63,186 | 24 / 44 MB |
| `mlp` | | CPU | 128 | 6.1 | 20,833 | – |
| `mlp` | | CPU | 256 | 6.6 | 38,696 | – |
| `small_cnn` | 288,746 | GPU | 128 | 11.8 | 10,878 | 149 / 226 MB |
| `small_cnn` | | GPU | 256 | 22.4 | 11,417 | 284 / 422 MB |
| `small_cnn` | | CPU | 128 | 126.3 | 1,013 | – |
| `small_cnn` | | CPU | 256 | 354.5 | 722 | – |

**Findings.**

- The GPU is faster for both models: 1.7× for the MLP and 11× for the CNN at batch 128.
- The parameter count does not predict which device wins: the CNN has a third of the MLP's parameters and gains far more from the GPU, because each of its parameters is used at every pixel. RUN-1.9's rule (ADR-0014 rule 8) only uses the count to decide *whether to time* both devices; the timing decides. That is the right design, and the benchmark is cheap: 20 CPU steps of the CNN take about 2.5 s.
- VRAM is no constraint at this scale: the CNN peaks at 420 MB reserved, against the 5.5 GB job budget (NFR-1).

## 3. A short real run

The small CNN, batch 128, Adam at 0.001, three epochs on the GPU through the loader with 4 workers:

| Epoch | Train seconds | Images/s | Val accuracy |
|---|---|---|---|
| 1 | 4.2 | 11,297 | 56.7 % |
| 2 | 3.8 | 12,634 | 68.0 % |
| 3 | 3.7 | 12,744 | 70.4 % |

Peak VRAM 149 MB allocated, 420 MB reserved. An epoch is about 4 s on the GPU; on the CPU it would be about 47 s (373 steps at 126 ms).

## 4. Checkpoints

Model and Adam state as one safetensors file plus a JSON file (no pickle, NFR-8), written to a temporary name, fsynced and renamed (ADR-0014 rule 5):

| Model | Tensors | Size | Save | Load |
|---|---|---|---|---|
| `small_cnn` | 78 | 3.5 MB | 18 ms | 1.5 ms |
| `mlp` | 18 | 9.9 MB | 27 ms | 0.6 ms |

**Finding.** A checkpoint every epoch costs well under 1 % of an epoch on either device. The 5-minute rule never fires for these models (an epoch is 4 s on the GPU and about 47 s on the CPU); it is there for larger ones. The cadence stays as decided.

## 5. Memory

The spike process peaked at 2,898 MB RSS, under the 4,500 MB limit. That peak includes the 575 MB preloaded tensor and the CPU timing, so a training run should use less. It does not include the loader's worker processes, which `ru_maxrss` cannot see. The training stage's tests will measure the container as a whole; ADR-0007's limits are not changed.

## Consequences

| ADR-0014 assumption | Result |
|---|---|
| Stream without a tensor cache (rule 4) | Holds with 4 loader workers on the GPU worker. On the CPU worker, 1 worker is enough (CPU training consumes about 1,000 images/s) |
| `auto` times both devices for models under 1 M parameters (rule 8) | Keep. Both reference models are under 1 M and both train faster on the GPU, by 1.7× and 11×; the count alone would have chosen wrongly, the timing does not |
| Checkpoint every epoch and at least every 5 minutes (rule 5) | Keep. 18–27 ms per checkpoint, 3.5–9.9 MB |
| Training fits the memory budget (ADR-0007) | Yes on the card (420 MB reserved); RAM to be confirmed per container when the stage runs, including loader workers |

**Loader workers (ADR-0007 rule 4).** Proposed starting values: 4 on `worker-gpu` (4.5 GB), 1 on `worker-cpu` (2.5 GB). The memory each worker process adds was not measured here, so the rule that derives the count from the container's limit is set once the training stage's tests measure the container as a whole.

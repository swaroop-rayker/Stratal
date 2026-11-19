# ADR-0017 — Model spec: layers, graphs, shape inference and compilation

**Status:** Proposed (M9, 2026-11-19), for the owner's approval. The four kickoff decisions it builds on were the owner's (2026-11-19): full graphs for BUILD-1.9, a tabular input block with one-hot by default, torchinfo as a test-only dependency, and BUILD-1.8 written by the agent.

## Context

- BUILD-1.1 to BUILD-1.9 and UI-2.3: build models from layers in the UI, with validated fields, shapes inferred and checked before training, a live parameter count and memory estimate, cross-field rules, templates, from-scratch attention, positional encoding and LSTM cell (👤 BUILD-1.8), and graphs with skip connections (P2).
- DATA-5.2, DATA-5.5: three dataset types, each declaring its compatible model families and default metrics; the UI offers only compatible combinations.
- ADR-0014 rule 2: training builds models through one factory, `build_model(spec, input_shape, num_classes)`. In M8 the spec names a reference model (`mlp`, `small_cnn`). M9's specs must compile through the same factory, so a built model trains, checkpoints, pauses and registers exactly like a reference one.
- ADR-0015 rule 1: a lineage's key includes the model spec. Existing lineages (`small_cnn · cifar10`, `mlp · cifar10`) and their config hashes must not change.
- The `api` image has no ML libraries ("API image has no ML libraries", DECISIONS.md), yet SCR-08 validates the spec on every edit.
- M10's open points 8 and 10 shape the spec: a tabular network's input width after encoding, and categorical columns through `Embedding`.

## Decision

### 1. A spec is a graph, and a chain is the common case

```json
{
  "name": "graph",
  "graph": {
    "spec_version": 1,
    "label": "my_cnn",
    "nodes": [
      {"id": "c1", "layer": {"type": "conv2d", "out_channels": 32, "kernel_size": 3, "padding": 1}},
      {"id": "a1", "layer": {"type": "relu"}},
      {"id": "p1", "layer": {"type": "max_pool2d", "kernel_size": 2}},
      {"id": "g",  "layer": {"type": "global_avg_pool"}}
    ],
    "edges": [["input", "c1"], ["c1", "a1"], ["a1", "p1"], ["p1", "g"], ["g", "output"]]
  }
}
```

- **Two fixed nodes, `input` and `output`,** not listed in `nodes`. Their shapes come from the dataset (rule 5); the user cannot edit them.
- **Edges are directed pairs.** The graph must be acyclic, every node must lie on a path from `input` to `output`, and `output` has exactly one incoming edge. A node with several incoming edges must be a merge layer (`add` or `concat`, rule 3); every other layer takes exactly one.
- **A chain is the same structure with one edge per node.** SCR-08's list view edits chains and shows skip edges as connectors; the canvas edits any edge (owner's decision: full graph, 2026-11-19).
- **Order and IDs are canonical.** Node IDs are short user-visible strings (`[a-z][a-z0-9_]{0,31}`). Nodes are stored in topological order with ties broken by ID, and edges sorted, so one structure has one JSON form (BUILD-1.7: a saved spec reloads identically).
- **`label` is cosmetic.** It names the model in lists and lineages. It is left out of the lineage key and the structure hash, so renaming a model does not start a new lineage.
- **Limits:** at most 200 nodes and 400 edges; a spec over them is refused with a field error.

### 2. The train config's `model` becomes a union, with old specs unchanged

`ModelSpec` becomes a discriminated union on `name`: `{"name": "mlp"}`, `{"name": "small_cnn"}` (the M8 reference models, serialized exactly as before) and `{"name": "graph", "graph": {...}}`. Existing config hashes, lineage keys and re-trains (REG-2.7) are therefore unchanged. The two reference models also ship as built-in templates written as graphs (rule 9), for anyone who wants to start from them on the canvas. Their graph form is a new spec, so a run started from it is a new lineage.

### 3. Layers are Pydantic models, and their schemas are the forms

One Pydantic model per layer type, `extra="forbid"`, every field with a range and a default, and a description that becomes the form's help text (BUILD-1.2, UI-2.3). `GET /model-specs/schema` returns the JSON Schema of the union; SCR-08's inspector renders the selected layer's part with rjsf, so a new field needs no UI code.

| Group | Types (`type`) | Fields the user sets (inferred fields in brackets) |
|---|---|---|
| Dense | `linear` | `out_features`, `bias`; [`in_features`: optional, inferred when null; a value that disagrees is an error, BUILD-1.4] |
| Convolution | `conv2d` | `out_channels`, `kernel_size`, `stride`, `padding`, `dilation`, `bias`; [`in_channels`] |
| Pooling | `max_pool2d`, `avg_pool2d`, `global_avg_pool` | `kernel_size`, `stride` (null: the kernel size), `padding` |
| Normalisation | `batch_norm`, `layer_norm` | `momentum`, `eps`; [`num_features`]. `batch_norm` is 1-d or 2-d by its input's rank |
| Regularisation | `dropout` | `p` in [0, 1) |
| Shape | `flatten` | none |
| Sequence | `embedding` | `embedding_dim`, `padding_idx`; [`num_embeddings`: the input's vocabulary] |
| Recurrent | `rnn`, `lstm`, `gru` | `hidden_size`, `num_layers`, `bidirectional`, `dropout`, `output` (`sequence` or `last`); `lstm` also `implementation` (`torch` or `scratch`, BUILD-1.8) |
| Attention | `attention`, `positional_encoding` | `num_heads`, `dropout`, `causal`; `max_len`. Both are BUILD-1.8's from-scratch units |
| Activations | `relu`, `leaky_relu`, `gelu`, `tanh`, `sigmoid`, `softmax` | `negative_slope` for `leaky_relu` |
| Merge | `add`, `concat` | `concat`: none (it joins along the first, channel or feature, axis) |

BUILD-1.1's list is covered; `global_avg_pool`, `avg_pool2d`, `layer_norm` and the merges are added because the reference CNN, a transformer block and BUILD-1.9's residual block need them. Training-time fields (epochs, optimizer, learning rate) stay in the train config (BUILD-1.2 for training parameters, already validated since M8).

### 4. Shape inference and counting are pure Python, in the API

`stratal.build` holds the layer models, the graph checks, shape inference, the parameter count and the memory estimate, **without PyTorch**, so `api` validates a spec on every edit (`POST /model-specs/validate`) and keeps no ML library.

- **A shape** is the per-sample shape without the batch axis, with a kind: `image (C, H, W)` float, `vector (F,)` float, `sequence (L, D)` float, `tokens (L,)` integer.
- **Each layer type declares** the input kinds it accepts, its output shape (the same arithmetic PyTorch uses, e.g. a convolution's `⌊(H + 2p − d(k − 1) − 1) / s⌋ + 1`), and its parameter count.
- **Problems** come back in words, each with its node and a stable code: `shape_mismatch` (BUILD-1.4), `rank_mismatch`, `below_one_pixel`, `heads_do_not_divide`, `odd_model_dim`, `cycle`, `unreachable`, `dangling`, `merge_needs_two`, `merge_shape_mismatch`, `too_many_inputs`, `output_not_vector`, `output_size`, `tokens_need_embedding`, `limit_exceeded`. Validation never raises for a bad spec: an invalid spec is a `200` with `problems`, and SCR-08 shows them as you type. Training refuses a spec with problems (`422 validation_error`, the same list).
- **Parity is tested, not assumed.** In the torch image, a seeded generator builds a few hundred random valid specs; for each, the compiled module's real output shapes and `sum(p.numel())` must equal the pure-Python figures, and torchinfo's count must agree (BUILD-1.5; torchinfo is test-only, approved 2026-11-19).

### 5. The dataset fixes the two ends (BUILD-1.3, DATA-5.2, DATA-5.5)

A **dataset type registry** (`stratal.data.dataset_types`, extended) declares for each type its input, its output, its compatible model families and its default metrics. `GET /dataset-types` serves it.

| Type | Input node | Output node | Families | Default metrics |
|---|---|---|---|---|
| `labelled_images` | `image (3, H, W)` from the version's image size | `vector (K,)`, K = classes | `mlp`, `cnn` (`transformer` needs a layer that cuts an image into patches; not in BUILD-1.1's list) | accuracy, macro F1 |
| `tabular` | `vector (F,)` from the tabular input block (rule 6) | `vector (K,)`, K = classes (regression in M10, open point 9) | `mlp` | accuracy, macro F1 |
| `labelled_text` | `tokens (L,)`, L = the version's maximum length, with its vocabulary size | `vector (K,)` | `mlp`, `rnn`, `transformer` | accuracy, macro F1 |

- **The output node is a locked head:** a `linear` from whatever the graph ends with to K, added at compile time. The graph must end in a vector (`output_not_vector` otherwise: flatten or pool first). Changing the dataset updates both locked ends at once, which is BUILD-1.3's acceptance criterion.
- **A graph's family is derived from its layers:** any `conv2d` makes it `cnn`; any recurrent layer `rnn`; any `attention` `transformer`; otherwise `mlp`. SCR-08 offers only compatible templates, and its palette disables layers whose family the type does not accept, with the reason (DATA-5.5).
- **In M9 only `labelled_images` can be trained.** Tabular and text ingestion arrive in M10 (DATA-1.4). Their schemas and input blocks are built and tested in M9 on synthetic column and vocabulary descriptions.

### 6. Tabular input: an input block, one-hot by default (M10 open points 8 and 10)

The `input` node of a tabular spec carries an **input block** with one entry per feature column: numeric columns are standardised (one feature each); categorical columns are one-hot encoded by default (one feature per category seen in the train split, plus one for unknown), or, if the column is set to `embedding`, mapped through an `Embedding` of a chosen width (default `min(50, ⌈cardinality / 2⌉)`). The block's width F is the sum, computed from the train split's statistics and **locked** in the builder. The encoding statistics are fitted on the train split only and stored with the weights (M10, ML-1.3). The owner chose this over one-hot only and embeddings only (2026-11-19).

### 7. Memory estimate (BUILD-1.5)

For P parameters in fp32:

| Part | Bytes |
|---|---|
| Weights | 4P |
| Gradients | 4P |
| Optimizer state | Adam 8P; SGD with momentum 4P; SGD without 0 |
| Activations (shown on SCR-09, per batch) | about 2 × 4 bytes × (sum of every node's output elements) × batch: what backward keeps, roughly |

SCR-08's summary shows the first three for the chosen optimizer (Adam by default). SCR-09 adds the activation estimate for the chosen batch and compares the total with the GPU cap (0.85 of the card) before the run starts. The activation figure is labelled as an estimate; the run's real peak is measured (NFR-1).

### 8. Compilation, in the worker

`build_model` compiles `{"name": "graph"}` to a `GraphModule`: one submodule per node under `nodes.<id>`, run in the stored topological order, plus the locked head. Parameter names are therefore stable (`nodes.c1.weight`), so safetensors written by one run load into the next (ADR-0015). Initialisation uses PyTorch's defaults under the run's seed, as the reference models do. A chain compiles to the same module type, so there is one code path.

### 9. Templates (BUILD-1.7)

A new table, `model_templates`: `id` (`mt_` + 12 hex), `name` (unique), `description`, `spec` (jsonb, canonical form), `structure_hash`, `dataset_type`, `builtin` (bool), `created_at`, `updated_at`. Endpoints `GET /model-templates?dataset_type=`, `POST /model-templates`, `GET`, `PUT` and `DELETE /model-templates/{id}`; built-in templates cannot be changed or deleted. Built-ins: MLP and small CNN (the reference models as graphs), a residual CNN (BUILD-1.9), an LSTM text classifier, a small transformer text classifier, and a tabular MLP. A saved spec reloads identically because it is stored in canonical form and compared by `structure_hash` and full equality.

### 10. BUILD-1.8: from-scratch units, written by the agent at the owner's request

Three units in `stratal.build.scratch`, each a `torch.nn.Module` with a typed contract and tests against PyTorch's reference within a stated tolerance, delivered first as stubs with strict expected-failure tests (ADR-0012), then written:
- **Multi-head self-attention** (`attention`): scaled dot-product, optional causal mask; compared with `torch.nn.MultiheadAttention` given the same weights.
- **Sinusoidal positional encoding** (`positional_encoding`): compared with the formula of *Attention Is All You Need*, computed independently in the test.
- **LSTM cell** (`lstm` with `implementation: scratch`): compared step by step with `torch.nn.LSTMCell` given the same weights.

The owner asked the agent to write them (2026-11-19); ADR-0012's log records it.

## Consequences

- **New endpoints:** `GET /model-specs/schema`, `POST /model-specs/validate`, `GET /dataset-types`, `/model-templates` (rule 9). `POST /jobs` with kind `train` accepts a graph spec and refuses one with problems.
- **New table:** `model_templates`, with a migration. No change to the registry tables: a graph's lineage key is its canonical spec without `label`.
- **New code:** `stratal.build` (pure Python, imported by `api` and the workers) and `stratal.build.compile` and `stratal.build.scratch` (PyTorch, workers only).
- **The frontend** gets SCR-08 (React Flow canvas plus a keyboard list view) and SCR-09, both through G2 first.
- **Not in M9:** training tabular or text models (M10), regression (M10), starting a run from a chosen trained version (`init_from`; its parent rule is in place since M8).

## Alternatives

| Option | Why not |
|---|---|
| Sequential stacks with a `residual` wrapper layer | Simpler, but the owner chose full graphs (2026-11-19) |
| Shape inference by compiling with PyTorch on a worker | A round trip through the queue on every keystroke, and PyTorch in the request path; the pure-Python version is checked against PyTorch by tests instead |
| `torch.fx` or ONNX as the spec format | Neither is a form a person edits, and neither validates ranges or explains problems in words |
| The user sets every `in_features` / `in_channels` | Every edit upstream would break downstream layers; inferred fields with an optional explicit value keep BUILD-1.4's check |
| A head the user must add | Changing the dataset would then break every model; a locked head is what BUILD-1.3 describes |
| Templates as files in `configs/` | Not editable from the UI, and not listable with their dataset type without a scan |

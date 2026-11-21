# ADR-0013 — Where semantic duplicates are found, and how the split uses them

**Status:** Accepted by the owner on 2026-11-17, as proposed. Built in M11 (DATA-2.3 and DATA-4.8 moved there from M7); nothing is built yet.

## Context

- **DATA-2.3** `[P2]` 👤: detect semantic duplicates by clustering CLIP embeddings. AC1: cropped variants grouped together; a report generated.
- **DATA-4.8** `[P2]` 👤: rows in one semantic cluster also join one split group. AC1: two photos of one scene from different angles (far apart by pHash, close by CLIP) land in the same split.
- The owner asked the agent to write both units (2026-11-17).
- ARCHITECTURE.md §3 says semantic duplicates "add a GPU sub-step" to `clean`. That cannot work as written:
  - `clean` runs on `worker-cpu`, which has no GPU and no PyTorch;
  - `clean` runs before `label`, the only stage that loads CLIP.
- Comparing every pair of 10,000 CLIP vectors (512 numbers each) is about 25 billion multiply-adds. That is fast as one matrix product on the GPU, and far too slow in pure Python. The CPU image has no NumPy, and adding it would need a requirement to justify it.
- `label` already computes every accepted image's CLIP vector to score its caption (LABEL-1.3).
- DATA-4.8 needs both photos *accepted*, so a semantic cluster cannot be a duplicate group in the existing sense: `clean` marks the non-kept members of a duplicate group as `duplicate`, and the split sees only accepted rows.

## Proposal

1. **Found in `label`, on the GPU.** After CLIP scoring, the stage compares the image vectors of the accepted rows (cosine similarity, one matrix product in batches) and joins pairs at or above a threshold with union-find, as DATA-4.2 does for hashes.
2. **Image vectors are cached** as a derivation (`clip_image_embedding`, float16, about 1 KB per image), so a resumed or rerun label stage clusters without re-encoding (NFR-5, LABEL-1.2).
3. **Detection, not removal, by default.** Each accepted row gets `semantic_cluster_id` (the cluster's smallest `sha256`, null for a row in no cluster). Status is unchanged. A config option `semantic_action: mark | remove` allows removing all but one per cluster (`semantic_duplicate`, `dup_kind = semantic`); the default is `mark`, because a "semantic duplicate" is often a different, useful photo.
4. **The split joins a cluster into one group.** `group_for_split` gains a rule: two items with the same non-null `semantic_cluster_id` are in the same group. The other rules and the carry-over (DATA-4.5) are unchanged.
5. **The report.** `label`'s version counts gain `semantic: {clusters, rows_in_clusters, largest}` and up to 20 example clusters; SCR-03 shows them like duplicate groups.
6. **The threshold** is a config value (`semantic_threshold`). Its default is chosen by measuring coco10k: the similarity of planted crops of the same image against unrelated pairs. A proposal comes with the measurement.
7. **Memoization.** `label`'s algorithm version and `split`'s scoring version are bumped, so no earlier version is reused for a run that should cluster (the M6 rule: change an output, bump its version).

## Alternatives

| Option | Why not (as proposed) |
|---|---|
| A `semantic` sub-step in `clean` (ARCHITECTURE as written) | `clean` has no GPU; moving it to the GPU queue would serialise all cleaning behind captioning |
| A new `semantic` GPU stage between `preprocess` and `label` | Loads CLIP a second time per run and adds a stage to every screen and test; it could run before captioning, which `label` cannot |
| Cluster in `filter` on the CPU with NumPy | Adds a dependency; still needs the vectors stored by `label` |
| Record clusters as duplicate groups | Both photos must stay accepted (DATA-4.8.AC1); duplicate groups keep one |

## Consequences

- The pipeline's stages are unchanged in number; `label` does one more GPU step (estimate: seconds for 10k vectors).
- Two manifest columns change: `semantic_cluster_id` (new); `dup_kind = semantic` used only with `semantic_action: remove`. DATA_MODEL.md, API.md (row filters) and SCR-03 need updates.
- Every existing label and split version stays as it is; new runs make new versions once.
- ARCHITECTURE.md §3's `clean` row is corrected.

## The threshold (2026-11-21)

The owner chose **0.90** from spike [S-10](../spikes/S-10-semantic-threshold.md), its recommendation: under `semantic_action: mark` a missed duplicate leaks across the split while a merged look-alike only coarsens it, and from 0.90 up union-find keeps clusters small (the largest 22 rows on coco10k; 260 at 0.85). A `remove` run should use 0.95 or more. Since M10, `worker-cpu` has NumPy; this does not change rule 1, since the label stage already holds the vectors on the GPU.

## Owner's decision (2026-11-17)

Approved as proposed, to be built in M11 with the other data features (UI-2.2, DATA-3.5). The threshold's default comes with its measurement, for the owner to choose (as OC-5).

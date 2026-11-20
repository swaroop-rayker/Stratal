# ADR-0015 — Model registry and weight retention

**Status:** Accepted by the owner on 2026-11-17, as proposed (M8). Addendum A (parents of fine-tuned versions) accepted by the owner on 2026-11-20 ("Approve both").

## Context

- REG-1.1, REG-1.2, REG-2.1 to REG-2.11, NFR-8, NFR-14, NFR-15; BUG7 (data bloat from training). The owner's decision 6: retention settings live in the database and are edited on SCR-14 (OC-9).
- DATA_MODEL §9 planned the tables: `model_lineages`, `model_versions`, `checkpoints`, `pins`, `retention_settings`.
- In-run checkpoints are defined by ADR-0014: one per running or paused job, in `jobs/<job_id>/checkpoints/`.
- Dataset versions are immutable and content-addressed (ADR-0004). Weights are neither: a run's result depends on hardware nondeterminism, and retention must be able to delete them.

## Decision

1. **A lineage is one model on one task.** It is created by the first run of a model spec on a dataset type, and holds:
   - **the base version:** the initial weights, saved before the first step (REG-2.1: "the initial weights of a from-scratch model"); never deleted by retention;
   - **one version per run:** `partial` while its run is paused, `trained` when it completes (REG-1.2). A run starts from the base or from a chosen version, recorded as the new version's parent.
2. **Every version records what made it** (REG-1.1): the resolved config and its hash, the dataset version, the job, metrics, the parent, and the weights' path, size and status (`present`, `deleting`, `weights_deleted`). IDs look like `mv_` and 12 hex characters.
3. **Weights are safetensors** with the model spec in JSON beside them, under `registry/<lineage>/<version>/`. Nothing in the registry is pickled (NFR-8).
4. **Retention is a job of kind `retention`** on the CPU queue. It runs after every completed run and on demand, with a dry run that changes nothing (REG-2.9). Per lineage it keeps:
   - the base;
   - the latest trained version;
   - the `keep_intermediate` most recent versions before it (global, overridable per lineage: REG-2.3, REG-2.4; `all` keeps everything);
   - pinned versions (REG-2.5);
   - any version in use: by a queued, running or paused job, loaded in the inference service, or the parent of another version's adapter (REG-2.8; adapters come in phase 4). Trying to delete one is refused and logged.
5. **A delete is atomic per version** (NFR-15). The job marks the version `deleting` and commits; removes its files; then sets `weights_deleted` with the bytes freed and commits again. A crash between the two leaves a `deleting` row, which the next retention run finishes. Metadata is never removed (REG-2.7), so a version whose weights are gone still shows its metrics and offers "re-train", which starts a run with its exact config and dataset version.
6. **Every deletion is an event** with the version and bytes freed (REG-2.9), so it appears in the job's activity log and in the history.
7. **Lowering `keep_intermediate` asks first** (REG-2.10). Saving a smaller N returns the plan: the versions that would go and their sizes. It is applied only when the owner confirms that plan; if anything changed in between, the confirmation is refused and a new plan shown.
8. **Disk use per lineage is measured on disk** (REG-2.11), split into base, latest, intermediate, pinned and checkpoints, and compared with the registry's own figures. NFR-14's bound (base + latest + one in-progress checkpoint, with N = 0) is the M8 disk-audit test, which is also BUG7's guard.

## Alternatives

| Option | Why not |
|---|---|
| Content-addressed weights, like dataset blobs | Two runs never produce identical weights, so nothing would be shared; deletion would need reference counting |
| Delete the metadata first | A crash would leave files no row points to: orphans NFR-15 forbids |
| Delete files without a `deleting` mark | A crash would leave a row pointing at missing files that still says `present` |
| Settings in `configs/retention.yaml` | The owner chose the database and SCR-14 (OC-9) |

## Consequences

- With default settings a lineage holds at most two weight files plus one checkpoint per active run.
- "Re-train" reproduces a deleted version's inputs exactly; its metrics match within NFR-4's tolerance, not exactly.
- Retention needs the inference service to report what it has loaded (ADR-0016).

## Addendum A — Parents of fine-tuned versions (RUN-1.10, REG-2.8), proposed 2026-11-20

**Status:** Accepted by the owner on 2026-11-20 ("Approve both"), as proposed, with ADR-0014's Addendum A (M11, kickoff K3 and K9).

### Context

- From M11 a run can start from a trained version (`init_from`, ADR-0014 Addendum A). Its version's parent can then be a trained version, possibly in another lineage, rather than its lineage's base.
- REG-2.8 says retention never deletes weights "in use by a running/queued job" or "the parent of an existing adapter/fine-tune". The owner's K9 reads the second as: a version is protected while it is the parent of a version that has weights.
- As built, two gaps and one difference:
  - **In use** is read from version rows whose job is active (`retention.in_use`). A fine-tune job that is queued, or a deep study between trials, has no version row yet, so its `init_from` is not protected.
  - **The parent rule** blocks a version with *any* child row (`policy.plan_lineage`, `children`). Metadata is never deleted (REG-2.7), so a version fine-tuned from even once would be kept forever. That is stricter than K9.
  - The children are already found across lineages, which a cross-lineage parent needs.

### Decision

1. **A version's parent** is its run's `init_from` when given, otherwise its lineage's base (rule 1 as before). The parent may be in another lineage.
2. **In use, widened (REG-2.8a).** On top of today's reasons, a version is in use when it is:
   - the `init_from` of a train job that is queued, running, paused or stalled, read from the job's stage config, so it holds before the job has a version row;
   - the `init_from` of a deep study that is running, read from the study's base, so it holds between trials.
3. **Parent rule, as K9 (REG-2.8b).** A version is blocked as a parent while at least one child has weights: `present`, `deleting`, or a `partial` child, whose weights are its run's checkpoint. A child whose weights are deleted no longer holds its parent. The refusal names the children that hold it, as now.
4. **Re-training a fine-tuned version (REG-2.7).** *Re-train* repeats the version's exact config, `init_from` included. If the parent's weights are gone too, the re-train is refused with `weights_deleted`, naming the parent, and the action says to re-train the parent first. A version re-trained that way matches within NFR-4's tolerance, not exactly, as every re-train does (Consequences, above).
5. **A lineage founded by a fine-tune** has the run's starting weights as its base (ADR-0014 Addendum A rule 6). A base is never deleted (rule 4), so that lineage's own runs never need the parent again. Its first version still names the parent, which rule 3 then protects only while that version keeps its weights.

### Alternatives

| Option | Why not |
|---|---|
| Keep today's rule (any child protects its parent) | Every fine-tuned-from version would be kept forever, since metadata is never deleted; disk use would grow with no way to free it |
| Delete a parent freely once its job ends | A child's *Re-train* and a queued fine-tune would find no weights; REG-2.8 forbids it |
| Pin the parent automatically | Pins are the owner's own choice (REG-2.5); an automatic pin would outlive its reason |

### Consequences

- `retention.in_use` and the facts given to `policy.plan_lineage` change; no schema change. The dry run (REG-2.9) shows the new reasons, e.g. "in use: job_0103 starts from it" and "parent of mv_… (weights present)".
- `test_REG_2_8_*` gains: a queued fine-tune's parent is blocked before the job starts; a parent whose only child's weights were deleted is deleted; a cross-lineage parent is blocked. `test_RUN_1_10_*` covers rule 4's refusal.
- With the default `keep_intermediate = 0`, fine-tuning the latest version and training again leaves the parent deletable once the child that holds it is gone.

# ADR-0015 — Model registry and weight retention

**Status:** Proposed by the agent on 2026-11-17 (M8). Waiting for the owner.

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

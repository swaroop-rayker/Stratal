# ADR-0004 — Immutable, content-addressed storage with manifest versions

**Status:** Accepted, 2026-11-13 (SPEC CL-12 and CL-17).

## Context

- DATA-3 asks for versions that are manifests pointing to files, with IDs derived from content and config, and lineage back to raw.
- BUG1: data after cleaning and preprocessing was not stored and retrieved properly.
- BUG7: data bloat and poor versioning.
- LABEL-1.2: captioning must resume after a crash without redoing finished images.
- Reading many small files from a Windows drive is slow from Linux ([ADR-0001](ADR-0001-docker-compose-on-wsl2.md)).

## Decision

1. **Blob store.** Every file Stratal uses is stored once, under the SHA-256 of its bytes. Blobs are read-only after writing.
2. **Ingest copies.** Each source file is copied into the blob store once. Later stages never touch the source folder.
3. **Manifests.** A dataset version is one immutable Parquet file with a row per item. Rows are never dropped. Each carries a status, the stage that decided it and a reason.
4. **Stages exchange version IDs.** A stage reads its parent version through one function, `load_version`, and writes a new version. Nothing is passed between stages in memory.
5. **Atomic writes.** Write to a temporary file, flush, rename. A version's database row is inserted only after its manifest exists.
6. **Memoized stages.** A stage with the same parent version and config as an existing version reuses it.
7. **Derivation cache.** Expensive per-item results (captions, CLIP scores, pHashes, image metadata) are stored by content hash plus the parameters that produced them, committed per batch.

How the version ID is derived (DATA-3.2), and how duplicates are detected and grouped (DATA-2), are owner-implemented. This ADR fixes only the storage they sit on.

## Alternatives

| Option | Why not |
|---|---|
| Reference source files in place | No copy needed, but every version breaks if the folder moves, and every stage pays the slow Windows read |
| Copy the dataset for each version | Simple, and exactly the bloat in BUG7 |
| DVC | It is one of the tools this project sets out to build (SPEC §7.6) |
| A faster non-cryptographic hash | SHA-256 is fast enough at this scale and makes "same hash means same file" safe to rely on |

## Consequences

- The raw data is stored twice: once where the owner keeps it, once in the blob store.
- Byte-identical duplicates cost nothing extra and are trivially detectable.
- Any version can be read at any time, and rereading gives the same result.
- Captioning and scoring become incremental: adding 500 images later captions only those 500.
- Unreferenced blobs need a garbage collector, built with retention in phase 2.
- The data is not a browsable folder tree. It is viewed through the dashboard, API or CLI.

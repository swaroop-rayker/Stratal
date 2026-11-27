# RAG question set v2 — draft for the owner's check

**Draft, 2026-11-27.** v1 stays frozen and unchanged. v2 is v1 with labels added; no question is added, removed or reworded, and the 15 unanswerable questions are untouched. File: `src/stratal/eval/rag/stratal-docs-questions-v2.jsonl`. Once approved, v2 is frozen, becomes the default set, and both reports are rebuilt.

## Why a v2

Measuring decision 11's reading (M12 acceptance) found most citation "misses" citing a chunk that states the same fact as the labelled one, from another document: the docs repeat facts across DECISIONS.md, SPEC.md's appendices, RISKS.md's spike results and the ADRs. v1 labelled one place per fact.

## How labels were added

- **Where looked:** for every answerable question, each non-labelled chunk in its top 5 on the 128-token index `ds_710b60b40962` (the five chunks the model is given), read in full. All 50 questions, not only the misses.
- **Rule:** a label is added only where the chunk itself states the answer. A chunk that gives background, the question's context, or only part of the answer gets none (examples left out: a01's API.md note on `dataset_version`, a11's ADR-0005 *As built* sections, a21's ADR-0008 measurements, a35's ADR-0016 proposal of "1 GB", a44's S-11 network notes).
- **Granularity:** a label names a section, as in v1. Where the added section is large (SPEC.md's *Appendix A — Change log*, DECISIONS.md's *Storage* or *Environment*), any chunk of that section now counts as a hit, including chunks that do not state the answer. This raises hit@k a little for those questions. Remove such a label if you prefer a stricter set.
- **Checked:** every added label exists as a document and section path in the index's chunks.

## Added labels (36 questions, 56 labels)

| ID | Question | Added (document › section) | What that chunk says |
|---|---|---|---|
| a02 | Why does a version ID use twelve hex characters rather than six? | `SPEC.md` › Appendix B — Open clarifications (not yet approved) | OC-4: twelve hex characters; "the examples' six collide too easily" |
| a03 | Are rows ever removed from a manifest when a stage rejects them? | `DECISIONS.md` › Storage<br>`DATA_MODEL.md` › 8. Invariants | "Rows are marked, never dropped"<br>INV-06: stages "never drop rows" |
| a04 | How is a blob written so that a crash leaves no partial file? | `DECISIONS.md` › Storage | "writes are temp-file-then-rename" |
| a05 | What does the derivations table cache, and what is its key? | `DECISIONS.md` › Storage<br>`decisions/ADR-0004…` › Decision | per-item results "keyed by content hash and parameter hash"<br>rule 7: stored "by content hash plus the parameters" |
| a07 | How long are raw resource samples kept before they are averaged? | `DECISIONS.md` › M7: phase-1 acceptance<br>`DATA_MODEL.md` › 4. … › 4.8a `resource_samples_1m`, … <br>`decisions/ADR-0005…` › As built in M7 (2026-11-17) | each: samples older than 7 days become one-minute averages |
| a08 | Why is PostgreSQL the system of record? | `DECISIONS.md` › Storage<br>`DATA_MODEL.md` › 1. Where data lives<br>`SPEC.md` › Appendix A — Change log v0.1 → v0.2 | "several processes write at once"<br>the same reason<br>CL-02: the input named two stores; PostgreSQL chosen |
| a09 | Why does each job run in its own child process? | `DECISIONS.md` › Job runtime<br>`SPEC.md` › Appendix A — Change log v0.1 → v0.2<br>`ERRORS.md` › Log › E-001 — … | "process exit is the only unload that cannot fail"<br>CL-01: the solo pool never released VRAM<br>one child process per job so weights cannot stay in VRAM |
| a12 | What happens to a running job when the laptop sleeps? | `DECISIONS.md` › GPU<br>`RISKS.md` › Spike plans › S-03 — … | sleep handling: keep-awake during jobs, sleep and resume reported<br>"the job simply paused and resumed" |
| a13 | Did a CUDA process survive a sleep and resume in the spike? | `RISKS.md` › Spike plans › S-03 — … | "a running GPU job survived sleep" |
| a14 | How does the UI receive live updates about a job? | `SCREENS.md` › 1. Rules that apply to every screen | "Live data arrives over SSE" |
| a16 | Which lock decides who may use the GPU? | `DECISIONS.md` › GPU | "A PostgreSQL advisory lock held by the user of the GPU" |
| a17 | What happened when PyTorch used more VRAM than the card has, without a cap? | `RISKS.md` › Spike plans › S-02 — What happens at the VRAM limit? (M0) | no error; it reached the script's 8 GB stop on a 6 GB card |
| a18 | What fraction of VRAM does every GPU process cap PyTorch's allocator at? | `ERRORS.md` › Log › E-005 — …<br>`RISKS.md` › Spike plans › S-02 — … | "caps PyTorch's allocator at 0.85"<br>"Cap at 0.85" |
| a19 | How much memory is the WSL virtual machine allowed to use? | `DECISIONS.md` › Environment<br>`RISKS.md` › Spike plans › S-07 — …<br>`SPEC.md` › Appendix A — Change log v0.1 → v0.2 | "The WSL VM is capped at 10 GB"<br>"against its 10 GB cap"<br>CL-03: "WSL VM capped at 10 GB" |
| a20 | Why was the RAM target restated as 9 GB across all containers? | `DECISIONS.md` › Environment<br>`SPEC.md` › Appendix A — Change log v0.1 → v0.2 | "15.2 GB usable and Windows needs a share. The input's 12 GB was not attainable"<br>CL-03: the same reason |
| a22 | Why did containers reject every HTTPS certificate, and how was it fixed? | `DECISIONS.md` › Environment<br>`decisions/ADR-0001…` › As implemented (2026-11-13) | Norton re-signs HTTPS; images trust its root from `docker/certs/`<br>the same |
| a23 | Why is Florence-2 loaded through the native transformers class? | `DECISIONS.md` › Models | "`trust_remote_code` runs code from the model repository, which NFR-8 rules out" |
| a24 | What CLIP similarity threshold marks two images as semantic duplicates? | `SPEC.md` › Appendix B — Open clarifications (not yet approved) | "a CLIP image similarity of 0.90, chosen by the owner" |
| a25 | Which captioner was chosen in spike S-06, Florence-2 or BLIP? | `SPEC.md` › 6. Open Questions | "Resolved: Florence-2-base … chosen by the owner after spike S-06" |
| a26 | Which code does the owner write and which does the agent write? | `decisions/ADR-0012…` › Context | the human owns core logic; the agent may write plumbing |
| a27 | Which branches are pushed to the remote repository? | `DECISIONS.md` › Process | "only `main` and milestone tags are pushed" |
| a28 | How do API errors reach clients? | `ARCHITECTURE.md` › 9. Cross-cutting concerns<br>`SPEC.md` › Appendix A — Change log v0.1 → v0.2 | the API maps errors to RFC 9457 problem details; jobs keep the same code<br>CL-10: typed errors → RFC 9457 with a stable `code` |
| a29 | How are requirement IDs used in commits and tests? | `DECISIONS.md` › Process<br>`SPEC.md` › 9. … › 9.1 General rules<br>`SPEC.md` › How to read this document | "IDs in commit scopes and test names"<br>"Every change references a requirement ID (commit, PR, test name)"<br>"the IDs used in commits, tests and pull requests" |
| a30 | How often are training checkpoints written? | `ARCHITECTURE.md` › 3. The phase-1 pipeline | "after every epoch and every `checkpoint_every_s`" |
| a31 | How does device auto decide between the CPU and the GPU? | `API.md` › 3. Endpoints (phase 1) › 3.3 Jobs | the GPU and CPU step times measured; when the CPU wins the job moves to the CPU queue |
| a32 | What does fine-tuning a trained version start from? | `decisions/ADR-0015…` › Addendum A — Parents of fine-tuned versions (…) › Context | "a run can start from a trained version (`init_from`)" |
| a33 | Can retention delete the weights of a pinned model version? | `API.md` › 3. … › 3.9 Model registry (…) | pin: "Exempt from retention (REG-2.5)" |
| a34 | What happens to a predict request while a training job holds the GPU? | `decisions/ADR-0016…` › Consequences<br>`API.md` › 3. … › 3.9 Model registry (…) | small models keep working on the CPU, more slowly<br>under 50 M parameters on the CPU, larger `409 gpu_busy` |
| a37 | What fixes a model's input and output shapes in the builder? | `SPEC.md` › 3. Functional requirements › Module BUILD — Model Builder<br>`SCREENS.md` › 3a. … › SCR-08 — Model builder canvas (M9) | BUILD-1.3.AC1: the dataset sets input and output<br>"input shape and output size, locked (BUILD-1.3)", from the version |
| a38 | How many times is the test split used in a hyperparameter study? | `SPEC.md` › 3. Functional requirements › Module HPO — Hyperparameter Search | "test is used once, on the best config" |
| a39 | Where does Optuna keep its own tables? | `DATA_MODEL.md` › 4. … › 4.15 HPO studies (…)<br>`DATA_MODEL.md` › 9. Planned for later phases | "Optuna's own tables live in schema `optuna`"<br>"Built in M10: schema `optuna`" |
| a40 | How is a tabular source turned into rows at ingest? | `decisions/ADR-0019…` › Addendum B — Tabular transforms (…) › Context | ingest coerces each value to its kind, features as JSON in `record`, the target in `label` or `target_value` |
| a42 | Why does a table transform refuse scaling and mean imputation? | `SCREENS.md` › 3a. … › SCR-15 — Transform editor (M11b) | "Fitted operations (scaling, imputation, target encoding) are the model's preprocessing (ML-1.3)" |
| a43 | What does a load or unload event record about VRAM? | `decisions/ADR-0006…` › Decision | rule 7: "VRAM before and after (MON-3.3), from NVML and from PyTorch" |
| a49 | What is the target time to the first token of an LLM answer? | `decisions/ADR-0021-llm-serving.md` › Context | "NFR-6: the first token in under 2 s" |
| a50 | Which fonts does the design system use? | `DESIGN-SYSTEM.md` › Open points for the owner | "IBM Plex Sans … and JetBrains Mono … are bundled" |

## Unchanged

a01, a06, a10, a11, a15, a21, a35, a36, a41, a44–a48: no chunk in their top 5 states the answer apart from their v1 labels. The 15 unanswerable questions (u01–u15).

## After approval

v2 becomes the default question set (`retrieval_eval` and the acceptance test); both index versions' retrieval reports are rebuilt on it; decision 11's reading is measured again on v2 and reported as it comes out, with v1's figure beside it.

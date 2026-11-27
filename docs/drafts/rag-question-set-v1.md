# RAG question set v1 — draft for the owner's check

M12 decision 6: the agent drafts, the owner checks every question and label before the first measurement; then the file is frozen (`src/stratal/eval/rag/stratal-docs-questions-v1.jsonl`, SHA-256 named in every report).

- **A label** is a document and a section path *without the document's title*, exactly as `chunk` writes it. A retrieved chunk is a hit when it comes from that document and its section is the labelled one or under it.
- Every label below was checked to exist in the real chunk version of the corpus (`ds_2fdf1423bfca`).
- Unanswerable questions do not reuse any example the docs themselves contain (for example the mockups' "What is the capital of Peru?", which the corpus does contain).

## Answerable (50)

| ID | Question | Answered by (document › section) |
|---|---|---|
| a01 | How is a dataset version ID derived? | `DATA_MODEL.md` › 3. Identifiers |
| a02 | Why does a version ID use twelve hex characters rather than six? | `DATA_MODEL.md` › 3. Identifiers |
| a03 | Are rows ever removed from a manifest when a stage rejects them? | `decisions/ADR-0004-immutable-content-addressed-storage.md` › Decision |
| a04 | How is a blob written so that a crash leaves no partial file? | `DATA_MODEL.md` › 6. Blob store<br>`ARCHITECTURE.md` › 6. Storage |
| a05 | What does the derivations table cache, and what is its key? | `DATA_MODEL.md` › 4. PostgreSQL schema › 4.9 `derivations` (content-addressed cache) |
| a06 | Which columns does the dataset_versions table have? | `DATA_MODEL.md` › 4. PostgreSQL schema › 4.2 `dataset_versions` |
| a07 | How long are raw resource samples kept before they are averaged? | `DATA_MODEL.md` › 10. Retention and growth |
| a08 | Why is PostgreSQL the system of record? | `decisions/ADR-0002-postgres-system-of-record.md` › Decision<br>`decisions/ADR-0002-postgres-system-of-record.md` › Context |
| a09 | Why does each job run in its own child process? | `decisions/ADR-0003-job-execution-model.md` › Decision<br>`ARCHITECTURE.md` › 4. Running a job › 4.1 Supervisor and child |
| a10 | What states can a job be in? | `ARCHITECTURE.md` › 4. Running a job › 4.2 Job states |
| a11 | How do events get from a running stage into PostgreSQL? | `decisions/ADR-0005-event-and-telemetry-pipeline.md` › Decision<br>`ARCHITECTURE.md` › 5. Events and telemetry › 5.1 Events |
| a12 | What happens to a running job when the laptop sleeps? | `decisions/ADR-0008-sleep-and-suspend-handling.md` › Decision<br>`ARCHITECTURE.md` › 8. Sleep and suspend |
| a13 | Did a CUDA process survive a sleep and resume in the spike? | `decisions/ADR-0008-sleep-and-suspend-handling.md` › Measured on 2026-11-14 (spike S-03) |
| a14 | How does the UI receive live updates about a job? | `API.md` › 4. Live streams |
| a15 | How do you retry a failed job through the API? | `API.md` › 3. Endpoints (phase 1) › 3.3 Jobs |
| a16 | Which lock decides who may use the GPU? | `decisions/ADR-0006-gpu-ownership-and-vram-policy.md` › Decision<br>`ARCHITECTURE.md` › 7. GPU and memory policy |
| a17 | What happened when PyTorch used more VRAM than the card has, without a cap? | `decisions/ADR-0006-gpu-ownership-and-vram-policy.md` › Measured on 2026-11-13 (spike S-02)<br>`ERRORS.md` › Log › E-005 — Exceeding VRAM does not fail; it silently spills and crawls |
| a18 | What fraction of VRAM does every GPU process cap PyTorch's allocator at? | `decisions/ADR-0006-gpu-ownership-and-vram-policy.md` › Measured on 2026-11-13 (spike S-02)<br>`ARCHITECTURE.md` › 7. GPU and memory policy |
| a19 | How much memory is the WSL virtual machine allowed to use? | `decisions/ADR-0007-memory-budget.md` › Decision |
| a20 | Why was the RAM target restated as 9 GB across all containers? | `decisions/ADR-0007-memory-budget.md` › Decision<br>`decisions/ADR-0007-memory-budget.md` › Context |
| a21 | How was the GPU fixed after Modern Standby made WSL lose it? | `ERRORS.md` › Log › E-012 — After Modern Standby, WSL lost the GPU |
| a22 | Why did containers reject every HTTPS certificate, and how was it fixed? | `ERRORS.md` › Log › E-002 — Containers reject every HTTPS certificate |
| a23 | Why is Florence-2 loaded through the native transformers class? | `decisions/ADR-0009-florence-2-native-transformers.md` › Decision<br>`decisions/ADR-0009-florence-2-native-transformers.md` › Context |
| a24 | What CLIP similarity threshold marks two images as semantic duplicates? | `decisions/ADR-0013-semantic-duplicates.md` › The threshold (2026-11-21)<br>`spikes/S-10-semantic-threshold.md` › Recommendation |
| a25 | Which captioner was chosen in spike S-06, Florence-2 or BLIP? | `DECISIONS.md` › M4: labeling<br>`RISKS.md` › Spike plans › S-06 — Florence-2 or BLIP? (M4) |
| a26 | Which code does the owner write and which does the agent write? | `decisions/ADR-0012-working-model-and-git-workflow.md` › Decision › Who writes what |
| a27 | Which branches are pushed to the remote repository? | `decisions/ADR-0012-working-model-and-git-workflow.md` › Decision › Git |
| a28 | How do API errors reach clients? | `decisions/ADR-0011-error-model.md` › Decision<br>`API.md` › 2. Errors |
| a29 | How are requirement IDs used in commits and tests? | `decisions/ADR-0010-requirement-ids-and-traceability.md` › Decision |
| a30 | How often are training checkpoints written? | `decisions/ADR-0014-training-jobs.md` › Decision |
| a31 | How does device auto decide between the CPU and the GPU? | `decisions/ADR-0014-training-jobs.md` › Decision |
| a32 | What does fine-tuning a trained version start from? | `decisions/ADR-0014-training-jobs.md` › Addendum A — Fine-tuning a trained version (RUN-1.10), proposed 2026-11-20 |
| a33 | Can retention delete the weights of a pinned model version? | `decisions/ADR-0015-model-registry-and-retention.md` › Decision |
| a34 | What happens to a predict request while a training job holds the GPU? | `decisions/ADR-0016-inference-service.md` › Decision |
| a35 | How much memory did the inference service use while serving small_cnn? | `decisions/ADR-0016-inference-service.md` › Built and measured (M8, 2026-11-18) |
| a36 | How does the model builder estimate a model's memory? | `decisions/ADR-0017-model-spec.md` › Decision › 7. Memory estimate (BUILD-1.5) |
| a37 | What fixes a model's input and output shapes in the builder? | `decisions/ADR-0017-model-spec.md` › Decision › 5. The dataset fixes the two ends (BUILD-1.3, DATA-5.2, DATA-5.5) |
| a38 | How many times is the test split used in a hyperparameter study? | `decisions/ADR-0018-hpo-execution.md` › Decision › 8. The test split is used once, on the best configuration (HPO-1.4) |
| a39 | Where does Optuna keep its own tables? | `decisions/ADR-0018-hpo-execution.md` › Decision › 3. Optuna's storage in its own schema; our tables are what the API reads<br>`spikes/S-09-hpo.md` › 1. Optuna's tables in their own schema |
| a40 | How is a tabular source turned into rows at ingest? | `decisions/ADR-0019-tabular-and-text-models.md` › Decision › A. Data in |
| a41 | Where does a text model's vocabulary come from? | `decisions/ADR-0019-tabular-and-text-models.md` › Decision › B. Text into tokens (open points 14–17) |
| a42 | Why does a table transform refuse scaling and mean imputation? | `decisions/ADR-0019-tabular-and-text-models.md` › Addendum B — Tabular transforms (DATA-7.1 to DATA-7.5, M11b) |
| a43 | What does a load or unload event record about VRAM? | `decisions/ADR-0020-alerts-and-diagnostics.md` › Decision › C. Load and unload events (MON-3.3) |
| a44 | Why can the ollama service not reach the internet? | `decisions/ADR-0021-llm-serving.md` › Decision › The `ollama` service |
| a45 | When does the inference service unload the LLM and give the GPU back? | `decisions/ADR-0021-llm-serving.md` › Decision › `inference` as Ollama's adapter (decision 3) |
| a46 | Why did the first GPU load of the LLM take about a minute in a new container? | `spikes/S-11-llm.md` › Loading, and the cold start<br>`decisions/ADR-0021-llm-serving.md` › Context |
| a47 | Which LLM was chosen for phase 3, and why? | `spikes/S-11-llm.md` › Recommendation<br>`spikes/S-11-llm.md` › Results |
| a48 | How are documents cut into chunks for retrieval? | `decisions/ADR-0022-corpora-and-retrieval.md` › Decision › `chunk` and `index` (LLM-2.1, decision 7) |
| a49 | What is the target time to the first token of an LLM answer? | `SPEC.md` › 4. Non-Functional Requirements |
| a50 | Which fonts does the design system use? | `DESIGN-SYSTEM.md` › b) Tokens › Typography |

## Unanswerable (15)

| ID | Question |
|---|---|
| u01 | What is the boiling point of water at sea level? |
| u02 | Who won the 2018 football World Cup? |
| u03 | How do I bake sourdough bread? |
| u04 | What is the population of Canada? |
| u05 | In which year did the Western Roman Empire fall? |
| u06 | How many moons does Jupiter have? |
| u07 | Who wrote the novel Pride and Prejudice? |
| u08 | How do I set up port forwarding on a home Wi-Fi router? |
| u09 | What is the chemical formula of table salt? |
| u10 | How tall is Mount Everest? |
| u11 | What is the speed of light in a vacuum? |
| u12 | Which planet is closest to the Sun? |
| u13 | How do I make a cup of masala chai? |
| u14 | What is the exchange rate between the euro and the yen? |
| u15 | Who painted the Mona Lisa? |

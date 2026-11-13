# ADR-0009 — Florence-2 through the native transformers implementation

**Status:** Accepted, 2026-11-13 (SPEC CL-08), pending the caption-quality check in spike S-06.

## Context

- LABEL-1.1 makes Florence-2-base the default captioner.
- The input's stack note says to pin the model revision and use `trust_remote_code`. That flag makes the library download and run Python code from the model repository at load time.
- NFR-8 forbids unsafe loading of untrusted files and requires downloads only from configured sources.
- transformers gained a native Florence-2 implementation in August 2025, with converted checkpoints published under `florence-community/`.
- Users report that the original `microsoft/` checkpoints, which depend on remote code, fail to load on newer transformers versions.

## Decision

- The default captioner is `florence-community/Florence-2-base`, loaded with the native `Florence2ForConditionalGeneration` class.
- The model revision is pinned to a commit hash in config. Weights load from safetensors.
- `trust_remote_code` is not used anywhere in the codebase. A test fails if the string appears in source.
- BLIP stays as the alternative for the 50-image comparison the input asks for.

## Alternatives

| Option | Why not |
|---|---|
| `microsoft/Florence-2-base` with a pinned revision and `trust_remote_code` | Pinning limits what code runs, but it still runs repository code, and it ties the project to old library versions |
| BLIP only | The input makes Florence-2 the default and asks for a measured comparison |

## Consequences

- The project needs a transformers release that includes the native class. Which release first shipped it has not been confirmed; it is pinned in the lockfile when the labeling dependencies are added.
- The converted checkpoints must caption as well as the originals. Spike S-06 checks quality and speed on 50 images, and its result decides the open question "Florence-2 vs BLIP".
- If S-06 shows a problem, this ADR is revisited before any 10k run.

## Sources

- [Transformers documentation: Florence-2](https://huggingface.co/docs/transformers/main/en/model_doc/florence2)
- [florence-community checkpoints](https://huggingface.co/florence-community)

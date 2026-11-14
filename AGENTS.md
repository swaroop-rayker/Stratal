# Stratal — agent instructions

The full instructions for coding agents are in [CLAUDE.md](CLAUDE.md). Read that file completely before doing anything in this repository. This file is kept short on purpose, so the two cannot drift apart.

The rules that matter most, repeated here in case only this file is loaded:

- Docs are the source of truth. If code and docs disagree, stop and ask.
- Never silently change a requirement; propose the change and wait.
- Every change references a requirement ID (commit, PR, test name).
- Never mark a requirement done without a passing `test_<ID>_*` test.
- Do NOT add a dependency unless a requirement needs it; state which.
- The human owns core logic (dedupe, versioning, batching/VRAM handling, scoring, evaluation, from-scratch layers). Do not implement it unless asked.
- No UI is implemented until its design is approved. Only an explicit "approved" passes a gate.
- Ask before any download, anything needing an account or key, any system setting, and any deletion of data.
- Never push. Never commit `docs/learning/`.

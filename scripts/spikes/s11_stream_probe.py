# pyright: basic, reportMissingImports=false
"""S-11 follow-up: what Qwen3 streams before its first visible token on a RAG prompt.

Runs beside the Ollama container like ``s11_llm.py``; prints, for each RAG prompt,
the prompt-eval time Ollama reports, the time of the first chunk of any kind, of
the first non-blank content, how many chunks came before it, and what they held.
``--think`` sends ``think: true`` instead of ``false``, to compare.
"""

import json
import sys
import time

sys.path.insert(0, "/app/scripts/spikes")
from s11_llm import _post, rag_prompts


def probe(model: str, think: bool) -> None:
    for messages in rag_prompts(10):
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
            "think": think,
            "keep_alive": "30m",
            "options": {"temperature": 0, "num_predict": 256},
        }
        started = time.monotonic()
        first_any = first_visible = None
        before: list[str] = []
        thinking = 0
        final = {}
        with _post("/api/chat", body) as response:
            for line in response:
                part = json.loads(line)
                now = time.monotonic() - started
                message = part.get("message") or {}
                if first_any is None:
                    first_any = now
                thinking += len(message.get("thinking") or "")
                content = message.get("content") or ""
                if first_visible is None:
                    if content.strip():
                        first_visible = now
                    else:
                        before.append(repr(content or message.get("thinking", ""))[:20])
                if part.get("done"):
                    final = part
        prompt_s = final.get("prompt_eval_duration", 0) / 1e9
        first_any = -1.0 if first_any is None else first_any
        first_visible = -1.0 if first_visible is None else first_visible  # no visible text
        print(
            f"prompt {final.get('prompt_eval_count')} tok in {prompt_s:.2f} s;"
            f" first chunk {first_any:.2f} s; first visible {first_visible:.2f} s after"
            f" {len(before)} chunks {before[:6]}; thinking chars {thinking};"
            f" eval {final.get('eval_count')} tok in {final.get('eval_duration', 0) / 1e9:.2f} s"
        )


if __name__ == "__main__":
    probe(sys.argv[1], "--think" in sys.argv)

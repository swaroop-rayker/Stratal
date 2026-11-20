// SCR-09's VRAM check (ADR-0017 rule 7, decision 6): the estimate against the cap, and the
// batch to offer when it is over. Warns, never blocks: a real out-of-memory run stops
// cleanly with its own suggestion (RUN-1.5).

export const CONTEXT_BYTES = 0.09 * 2 ** 30; // the CUDA context (ADR-0006 rule 9)
export const CAP_SHARE = 0.85; // PyTorch's per-process cap (ADR-0006 rule 8)

export interface VramCheck {
  fixed: number;
  activations: number;
  context: number;
  total: number;
  cap: number;
  card: number;
  fits: boolean;
  /** The largest power of two below ``batch`` that fits by the estimate, when over. */
  suggested: number | null;
}

export function vramCheck(fixedBytes: number, perSampleBytes: number, batch: number, cardMb: number): VramCheck {
  const card = cardMb * 2 ** 20;
  const cap = CAP_SHARE * card;
  const activations = perSampleBytes * batch;
  const total = fixedBytes + activations + CONTEXT_BYTES;
  const fits = total <= cap;
  let suggested: number | null = null;
  if (!fits) {
    for (let b = 2 ** Math.floor(Math.log2(Math.max(batch - 1, 1))); b >= 1; b = Math.floor(b / 2)) {
      if (b < batch && fixedBytes + perSampleBytes * b + CONTEXT_BYTES <= cap) {
        suggested = b;
        break;
      }
      if (b === 1) break;
    }
  }
  return { fixed: fixedBytes, activations, context: CONTEXT_BYTES, total, cap, card, fits, suggested };
}

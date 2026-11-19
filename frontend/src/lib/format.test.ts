// UI-2.1: live numbers are formatted the same way everywhere; unknown is a dash, not zero.
import { describe, expect, test } from "vitest";

import { DASH, bytes, duration, errorDetail, gb, percent, stagePercent } from "./format";

describe("format", () => {
  test("test_UI_2_1_unknown_values_are_a_dash_never_zero", () => {
    expect(percent(null)).toBe(DASH);
    expect(gb(undefined)).toBe(DASH);
    expect(duration(null)).toBe(DASH);
    expect(stagePercent(5, null)).toBeNull();
  });

  test("test_UI_2_1_durations_sizes_and_percentages", () => {
    expect(duration(872)).toBe("14m 32s");
    expect(duration(7500)).toBe("2h 05m");
    expect(duration(48)).toBe("48s");
    expect(gb(7372)).toBe("7.2");
    expect(bytes(1_632_004)).toBe("1.6 MB");
    expect(stagePercent(42, 313)).toBeCloseTo(13.4, 1);
    expect(percent(94.4)).toBe("94%");
  });
});

describe("errorDetail (E-013)", () => {
  test("keeps a sentence and reads facts as words", () => {
    expect(errorDetail("disk full")).toBe("disk full");
    expect(errorDetail({ signal: "SIGKILL", peak_rss_mb: 2410, limit_mb: null })).toBe("signal SIGKILL · peak rss mb 2410");
    expect(errorDetail(null)).toBeNull();
    expect(errorDetail({})).toBeNull();
  });
});

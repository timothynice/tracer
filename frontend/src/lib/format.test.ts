import { formatBytes, formatInt, formatMs, formatPercent } from "./format";

test("formatBytes", () => {
  expect(formatBytes(0)).toBe("0 B");
  expect(formatBytes(942)).toBe("942 B");
  expect(formatBytes(3878)).toBe("3.8 KB");
  expect(formatBytes(120_000)).toBe("117 KB");
  expect(formatBytes(2_500_000)).toBe("2.38 MB");
  expect(formatBytes(null)).toBe("–");
});

test("formatMs", () => {
  expect(formatMs(0.4)).toBe("<1 ms");
  expect(formatMs(12.6)).toBe("13 ms");
  expect(formatMs(1300)).toBe("1.3 s");
  expect(formatMs(15_000)).toBe("15 s");
});

test("formatInt / formatPercent", () => {
  expect(formatInt(12345.6)).toBe("12,346");
  expect(formatPercent(0.256)).toBe("26%");
});

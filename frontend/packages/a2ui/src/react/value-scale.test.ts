import { describe, expect, it } from "vitest";
import { logAxisFloor, resolveValueScale } from "./charts";

const series = [{ key: "v" }];
const rows = (...values: number[]) => values.map((v) => ({ v }));

describe("resolveValueScale", () => {
  it("keeps a linear axis for same-magnitude data", () => {
    expect(resolveValueScale("auto", rows(3, 8, 12, 40), series)).toBe("linear");
  });
  it("switches to log when positive values span >= 100x", () => {
    expect(resolveValueScale("auto", rows(0.1, 0.5, 5, 30), series)).toBe("log");
    expect(resolveValueScale(undefined, rows(1, 100), series)).toBe("log");
  });
  it("never picks log when a value is zero or negative", () => {
    expect(resolveValueScale("auto", rows(0, 5, 500), series)).toBe("linear");
    expect(resolveValueScale("auto", rows(-1, 5, 500), series)).toBe("linear");
  });
  it("honours an explicit scale", () => {
    expect(resolveValueScale("log", rows(1, 2, 3), series)).toBe("log");
    expect(resolveValueScale("linear", rows(0.1, 100), series)).toBe("linear");
  });
  it("looks across every series", () => {
    const multi = [{ key: "a" }, { key: "b" }];
    expect(resolveValueScale("auto", [{ a: 1, b: 2 }, { a: 3, b: 400 }], multi)).toBe("log");
  });

  it("keeps stacked series linear in auto mode", () => {
    const data = [{ x: "a", s1: 1, s2: 1000 }];
    expect(resolveValueScale("auto", data, [{ key: "s1", stackId: "total" }, { key: "s2", stackId: "total" }])).toBe("linear");
    expect(resolveValueScale("log", data, [{ key: "s1", stackId: "total" }])).toBe("log");
  });

  it("floors a log axis one power of ten under half the smallest value", () => {
    expect(logAxisFloor(0.1)).toBeCloseTo(0.01);
    expect(logAxisFloor(1)).toBeCloseTo(0.1);
    expect(logAxisFloor(5)).toBe(1);
    expect(logAxisFloor(0.12)).toBeCloseTo(0.01);
    expect(logAxisFloor(0)).toBeCloseTo(0.1);
  });
});

import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ServiceInfo } from "@valuz/shared";
import { StartupScreen } from "./StartupScreen";

const svc = (status: ServiceInfo["status"]): ServiceInfo[] => [
  { name: "backend", status, port: 8000 } as ServiceInfo,
];

const pct = (el: HTMLElement) =>
  Number(el.querySelector(".splash-progress-pct")?.textContent?.replace("%", ""));

describe("StartupScreen boot progress", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("advances continuously while waiting instead of parking at a fixed ratio", () => {
    const { container } = render(
      <StartupScreen
        services={svc("starting")}
        logs={[]}
        loading={true}
        error={null}
        onRetry={async () => {}}
      />,
    );
    const samples: number[] = [];
    for (let i = 0; i < 6; i += 1) {
      act(() => vi.advanceTimersByTime(1_500));
      samples.push(pct(container));
    }
    // strictly climbing, but never claiming to be done while still booting
    for (let i = 1; i < samples.length; i += 1) {
      expect(samples[i]).toBeGreaterThan(samples[i - 1]);
    }
    expect(samples[samples.length - 1]).toBeGreaterThan(30);
    expect(samples[samples.length - 1]).toBeLessThan(93);
  });

  it("runs to 100% once every service is running and freezes on error", () => {
    const { container, rerender } = render(
      <StartupScreen
        services={svc("starting")}
        logs={[]}
        loading={true}
        error={null}
        onRetry={async () => {}}
      />,
    );
    act(() => vi.advanceTimersByTime(3_000));
    const before = pct(container);
    rerender(
      <StartupScreen
        services={svc("running")}
        logs={[]}
        loading={false}
        error={null}
        onRetry={async () => {}}
      />,
    );
    act(() => vi.advanceTimersByTime(3_000));
    expect(pct(container)).toBe(100);
    expect(pct(container)).toBeGreaterThan(before);

    const failed = render(
      <StartupScreen
        services={svc("error")}
        logs={[]}
        loading={false}
        error="boom"
        onRetry={async () => {}}
      />,
    );
    act(() => vi.advanceTimersByTime(3_000));
    expect(pct(failed.container)).toBe(0);
  });
});

describe("boot pacing learns from this machine's previous boots", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
  });
  afterEach(() => vi.useRealTimers());

  it("uses the median of recorded boots (clamped) and falls back to a default", async () => {
    const { estimateBootSeconds, pacedTarget } = await import("./StartupScreen");
    expect(estimateBootSeconds([])).toBe(6);
    expect(estimateBootSeconds([2000, 2500, 30000])).toBe(2.5); // outlier ignored
    expect(estimateBootSeconds([100])).toBe(1.5); // floor
    // linear to 85% at the estimate, then a slow asymptote below 92
    expect(pacedTarget(1, 2)).toBeCloseTo(42.5, 1);
    expect(pacedTarget(2, 2)).toBeCloseTo(85, 1);
    expect(pacedTarget(20, 2)).toBeLessThan(92);
    expect(pacedTarget(20, 2)).toBeGreaterThan(85);
  });

  it("paces a fast machine faster and records the real boot duration", () => {
    localStorage.setItem("valuz-boot-durations", JSON.stringify([2000, 2000, 2000]));
    const { container, rerender } = render(
      <StartupScreen
        services={svc("starting")}
        logs={[]}
        loading={true}
        error={null}
        onRetry={async () => {}}
      />,
    );
    act(() => vi.advanceTimersByTime(1_000));
    // ~42% at half the 2s estimate (eased, so a touch below the raw target)
    expect(pct(container)).toBeGreaterThan(35);
    rerender(
      <StartupScreen
        services={svc("running")}
        logs={[]}
        loading={false}
        error={null}
        onRetry={async () => {}}
      />,
    );
    act(() => vi.advanceTimersByTime(500));
    const stored = JSON.parse(localStorage.getItem("valuz-boot-durations") ?? "[]");
    expect(stored).toHaveLength(4);
    expect(stored[3]).toBeGreaterThanOrEqual(1_000);
  });
});

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

import { beforeEach, describe, expect, it } from "vitest";

import { useUpdaterStore } from "./updater-store";

const s = () => useUpdaterStore.getState();

describe("updater-store: macOS install hand-off detection", () => {
  beforeEach(() => s().reset());

  it("flips to 'preparing' on the second-pass reset and holds the bar full", () => {
    s().setDownloading();
    // pass 1 — real network download 0 → 100
    for (const p of [10, 50, 95, 100]) s().setProgress(p, 1_000);
    expect(s().status).toBe("downloading");
    expect(s().progress).toBe(100);

    // pass 2 — Squirrel.Mac re-reads from loopback, progress resets low
    s().setProgress(3, 50_000);
    expect(s().status).toBe("preparing");
    expect(s().progress).toBe(100);

    // further fast pass-2 events stay 'preparing', bar stays full
    s().setProgress(60, 50_000);
    expect(s().status).toBe("preparing");
    expect(s().progress).toBe(100);

    s().setDownloaded();
    expect(s().status).toBe("downloaded");
  });

  it("does not trip on small jitter near the end", () => {
    s().setDownloading();
    s().setProgress(95, 1_000);
    s().setProgress(92, 1_000); // -3, not a reset
    expect(s().status).toBe("downloading");
    expect(s().progress).toBe(92);
  });

  it("does not trip on a backward blip below 90%", () => {
    s().setDownloading();
    s().setProgress(40, 1_000);
    s().setProgress(28, 1_000); // big drop, but we hadn't reached >=90
    expect(s().status).toBe("downloading");
    expect(s().progress).toBe(28);
  });

  it("single-pass download (no reset) never enters 'preparing'", () => {
    s().setDownloading();
    for (const p of [25, 50, 75, 100]) s().setProgress(p, 1_000);
    expect(s().status).toBe("downloading");
    s().setDownloaded();
    expect(s().status).toBe("downloaded");
  });
});

describe("updater-store: error phase", () => {
  beforeEach(() => s().reset());

  it("marks the phase 'download' when the error interrupts a download", () => {
    s().setDownloading();
    s().setError("net::ERR_CONTENT_LENGTH_MISMATCH", true);
    expect(s().errorPhase).toBe("download");
  });

  it("marks the phase 'download' when the error interrupts the install hand-off", () => {
    s().setDownloading();
    for (const p of [95, 3]) s().setProgress(p, 1_000); // enters "preparing"
    expect(s().status).toBe("preparing");
    s().setError("boom", true);
    expect(s().errorPhase).toBe("download");
  });

  it("marks the phase 'check' when the error interrupts a check", () => {
    s().setChecking();
    s().setError("net::ERR_NAME_NOT_RESOLVED", true);
    expect(s().errorPhase).toBe("check");
  });

  it("clears the phase on reset", () => {
    s().setDownloading();
    s().setError("boom", true);
    s().reset();
    expect(s().errorPhase).toBeNull();
  });
});

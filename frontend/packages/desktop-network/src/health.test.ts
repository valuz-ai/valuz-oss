import { describe, expect, it } from "vitest";
import { applyConnectionOutcome } from "./health";
import type { EgressSnapshot } from "./types";

const baseSnapshot = (): EgressSnapshot => ({
  connectionAttemptId: "attempt-1",
  clientId: "client-1",
  activeTurn: false,
  requestActive: false,
  runtime: "codex",
  frontend: "shadow",
  targetOrigin: "https://api.example",
  mode: "auto",
  route: "direct",
  health: "unknown",
  resolveMs: 3,
  reconnectCount: 0,
  fallbackCount: 0,
  correlationConfidence: "exact_runtime",
  updatedAt: 10,
});

describe("applyConnectionOutcome", () => {
  it("marks a fast first-candidate connection healthy", () => {
    expect(
      applyConnectionOutcome(
        baseSnapshot(),
        { success: true, connectMs: 40 },
        { now: () => 20 },
      ),
    ).toMatchObject({
      health: "healthy",
      connectMs: 40,
      fallbackCount: 0,
      reconnectCount: 0,
      updatedAt: 20,
    });
  });

  it("marks fallback, reconnect and over-budget successes degraded", () => {
    expect(
      applyConnectionOutcome(baseSnapshot(), {
        success: true,
        connectMs: 40,
        fallbackCount: 1,
      }).health,
    ).toBe("degraded");
    expect(
      applyConnectionOutcome(baseSnapshot(), {
        success: true,
        connectMs: 40,
        reconnectCount: 1,
      }).health,
    ).toBe("degraded");
    expect(
      applyConnectionOutcome(
        baseSnapshot(),
        { success: true, connectMs: 101 },
        { connectDegradedMs: 100 },
      ).health,
    ).toBe("degraded");
  });

  it("marks candidate exhaustion failed with a stable code", () => {
    expect(
      applyConnectionOutcome(baseSnapshot(), {
        success: false,
        errorCode: "proxy_refused",
      }),
    ).toMatchObject({
      health: "failed",
      lastErrorCode: "proxy_refused",
    });
  });
});

import { describe, expect, it } from "vitest";
import { sendStillOwnsPage } from "./conversation-send-ownership";
import { NEW_SESSION_ID } from "./conversation/session-events";

describe("sendStillOwnsPage", () => {
  it("a follow-up on the open session owns the page", () => {
    expect(
      sendStillOwnsPage({
        routeId: "s",
        routeEpoch: 3,
        epochAtSend: 3,
        sessionId: "s",
      }),
    ).toBe(true);
  });

  it("a draft that has not moved yet owns the page", () => {
    expect(
      sendStillOwnsPage({
        routeId: NEW_SESSION_ID,
        routeEpoch: 0,
        epochAtSend: 0,
        sessionId: "minted",
      }),
    ).toBe(true);
  });

  it("a draft promoted to the id it minted owns the page", () => {
    // The promote navigation itself bumps the epoch — the page moved, but
    // onto exactly this session.
    expect(
      sendStillOwnsPage({
        routeId: "minted",
        routeEpoch: 1,
        epochAtSend: 0,
        sessionId: "minted",
      }),
    ).toBe(true);
  });

  it("opening another conversation during the create loses the page", () => {
    expect(
      sendStillOwnsPage({
        routeId: "other",
        routeEpoch: 1,
        epochAtSend: 0,
        sessionId: "minted",
      }),
    ).toBe(false);
  });

  it("opening another conversation after the promotion loses the page", () => {
    expect(
      sendStillOwnsPage({
        routeId: "other",
        routeEpoch: 2,
        epochAtSend: 0,
        sessionId: "minted",
      }),
    ).toBe(false);
  });

  it("leaving and coming back to a fresh draft loses the page", () => {
    // Same route id as when the send started, but the page left and came
    // back — that draft is a new one, not the one the message was typed into.
    expect(
      sendStillOwnsPage({
        routeId: NEW_SESSION_ID,
        routeEpoch: 2,
        epochAtSend: 0,
        sessionId: "minted",
      }),
    ).toBe(false);
  });

  it("a follow-up whose page moved to another session loses the page", () => {
    expect(
      sendStillOwnsPage({
        routeId: "other",
        routeEpoch: 4,
        epochAtSend: 3,
        sessionId: "s",
      }),
    ).toBe(false);
  });
});

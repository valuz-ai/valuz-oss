import { describe, expect, it } from "vitest";
import { connectorStatusView } from "./connector-status";

describe("connectorStatusView", () => {
  it("reports a switched-off connector as off, not as whatever failed last", () => {
    // The bug this closes: nothing probes a disabled connector, so its stored
    // status is the verdict from whenever it was last on — possibly weeks ago.
    // Rendering that verdict made "off" and "broken right now" identical.
    expect(connectorStatusView({ status: "error", enabled: false })).toEqual({
      status: "disabled",
      labelKey: "connector.statusDisabled",
    });
  });

  it("prefers off-ness over a healthy status too", () => {
    // Otherwise a connector the owner turned off keeps claiming 已连接 — the
    // same ambiguity with the colours swapped.
    expect(
      connectorStatusView({ status: "connected", enabled: false }),
    ).toEqual({
      status: "disabled",
      labelKey: "connector.statusDisabled",
    });
  });

  it("passes an enabled connector's status straight through", () => {
    expect(connectorStatusView({ status: "connected", enabled: true })).toEqual(
      {
        status: "connected",
        labelKey: "connector.statusConnected",
      },
    );
    expect(connectorStatusView({ status: "error", enabled: true })).toEqual({
      status: "error",
      labelKey: "connector.statusError",
    });
  });

  it("collapses both not-connected-yet states onto one label", () => {
    // pending_auth vs unknown is how far setup got — not something a one-word
    // pill can carry, and not something the reader can act on differently.
    const pending = connectorStatusView({ status: "pending_auth" });
    const unknown = connectorStatusView({ status: "unknown" });
    expect(pending?.labelKey).toBe("connector.statusNotConnected");
    expect(unknown?.labelKey).toBe("connector.statusNotConnected");
    // The tone still differs per raw status, so they keep their own colours.
    expect(pending?.status).toBe("pending_auth");
    expect(unknown?.status).toBe("unknown");
  });

  it("treats a missing enabled flag as not-disabled", () => {
    // Some callers build their row from a source that has no enabled field
    // (an agent's bound-but-uninstalled connector). Absent must not read as off.
    expect(connectorStatusView({ status: "connected" })?.status).toBe(
      "connected",
    );
  });

  it("renders no pill for a status it does not recognize", () => {
    // Better nothing than a raw backend word leaking into the UI.
    expect(connectorStatusView({ status: "reticulating" })).toBeNull();
    expect(connectorStatusView({})).toBeNull();
  });
});

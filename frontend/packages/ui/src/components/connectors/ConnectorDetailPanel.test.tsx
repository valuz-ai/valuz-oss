/** @vitest-environment jsdom */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConnectorDetailPanel } from "./ConnectorDetailPanel";

describe("ConnectorDetailPanel", () => {
  it.each([false, true])(
    "renders edition-provided header actions when connected=%s",
    (connected) => {
      render(
        <ConnectorDetailPanel
          name="Shared Connector"
          connected={connected}
          tools={connected ? [] : undefined}
          headerActions={<button type="button">Publish</button>}
        />,
      );

      expect(screen.getByRole("button", { name: "Publish" })).toBeTruthy();
    },
  );

  // A built-in used to render its disconnect disabled, which combined with the
  // Connect button only existing for a not-connected connector: once the grant
  // died, the panel showed "connected", offered no reconnect, and refused the
  // one action that could have cleared it. The label stays; the button works.
  it("lets a system-managed connector be disconnected", () => {
    let disconnected = false;
    render(
      <ConnectorDetailPanel
        name="Valuz · Search"
        connected
        tools={[]}
        systemManaged
        onDisconnect={() => {
          disconnected = true;
        }}
      />,
    );

    const button = screen.getByRole("button", { name: /disconnect|断开/i });
    expect((button as HTMLButtonElement).disabled).toBe(false);
    button.click();
    expect(disconnected).toBe(true);
  });

  it("has nothing to click when no disconnect is offered", () => {
    render(
      <ConnectorDetailPanel name="Read Only" connected tools={[]} systemManaged />,
    );

    const button = screen.getByRole("button", { name: /disconnect|断开/i });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("does not offer a local connect action for a read-only catalog detail", () => {
    render(
      <ConnectorDetailPanel
        name="Organization Connector"
        connected={false}
        headerActions={<button type="button">Download</button>}
      />,
    );

    expect(screen.getByRole("button", { name: "Download" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
  });
});

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
      <ConnectorDetailPanel
        name="Read Only"
        connected
        tools={[]}
        systemManaged
      />,
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

  it("opens the edit form when the connector's configuration is the user's own", () => {
    let edited = false;
    render(
      <ConnectorDetailPanel
        name="My Local Server"
        connected
        tools={[]}
        onEdit={() => {
          edited = true;
        }}
      />,
    );

    screen.getByRole("button", { name: /^(edit|编辑)$/i }).click();
    expect(edited).toBe(true);
  });

  it("offers no edit action when the caller passes no onEdit", () => {
    render(
      <ConnectorDetailPanel
        name="Valuz · Search"
        connected
        tools={[]}
        systemManaged
      />,
    );

    expect(screen.queryByRole("button", { name: /^(edit|编辑)$/i })).toBeNull();
  });

  // Placement is the requirement, not decoration: Edit sits between the
  // overlay's header actions and Disconnect, so the destructive action stays
  // last in the row.
  it("places edit after the overlay actions and before disconnect", () => {
    render(
      <ConnectorDetailPanel
        name="My Local Server"
        connected
        tools={[]}
        headerActions={<button type="button">Copy</button>}
        onEdit={() => {}}
        onDisconnect={() => {}}
      />,
    );

    const labels = screen
      .getAllByRole("button")
      .map((b) => (b.textContent ?? "").trim());
    const copy = labels.findIndex((l) => l === "Copy");
    const edit = labels.findIndex((l) => /^(edit|编辑)$/i.test(l));
    const disconnect = labels.findIndex((l) => /disconnect|断开/i.test(l));

    expect(copy).toBeGreaterThanOrEqual(0);
    expect(copy).toBeLessThan(edit);
    expect(edit).toBeLessThan(disconnect);
  });
});

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { initI18n } from "@valuz/shared/i18n";
import {
  connectorsApi,
  useCategoryRegistry,
  useRegistryStore,
} from "@valuz/core";
import type { ConnectorItem } from "@valuz/core";
import { ConnectorsPane } from "./ConnectorsPane";

const organizationConnector = {
  id: "org-connector-1",
  slug: "team-search",
  display_name: "Team Search",
  description: "Shared by the organization",
  connector_type: "custom",
  transport: "http",
  url: "https://example.com/mcp",
  auth_type: "none",
  has_api_key: false,
  command: null,
  args: [],
  working_dir: null,
  env: {},
  headers: [],
  params: [],
  enabled: true,
  status: "unknown",
  tool_count: null,
  last_tested_at: null,
  error_message: null,
  created_at: 0,
  updated_at: 0,
  _sync: {
    status: "cloud_only",
    cloud_id: "org-connector-1",
    scope: "org",
  },
} as unknown as ConnectorItem;

const downloadedOrganizationConnector = {
  ...organizationConnector,
  id: "local-connector-1",
  _sync: {
    status: "synced",
    cloud_id: "org-connector-1",
    scope: "org",
  },
} as unknown as ConnectorItem;

const personalBuiltinConnector = {
  ...organizationConnector,
  id: "builtin-connector-1",
  slug: "personal-search",
  display_name: "Personal Search",
  connector_type: "builtin",
  _sync: undefined,
} as unknown as ConnectorItem;

describe("ConnectorsPane extension slots", () => {
  beforeEach(() => {
    initI18n({ locale: "en-US", fallbackLocale: "en-US" });
    vi.spyOn(connectorsApi, "list").mockResolvedValue({
      connectors: [organizationConnector],
    });
    vi.spyOn(connectorsApi, "listDirectory").mockResolvedValue({ items: [] });
  });

  afterEach(() => {
    act(() => {
      useRegistryStore
        .getState()
        .unregisterSlot("resource.connector.actions", "test-mcp-download");
      useRegistryStore
        .getState()
        .unregisterSlot(
          "resource.connector.detail.actions",
          "test-organization-actions",
        );
      useCategoryRegistry.getState().remove("connector");
    });
    vi.restoreAllMocks();
  });

  it("renders organization actions in a local connector detail", async () => {
    const localConnector = {
      ...organizationConnector,
      id: "local-connector-1",
      slug: "local-search",
      display_name: "Local Search",
      _sync: undefined,
    } as unknown as ConnectorItem;
    vi.mocked(connectorsApi.list).mockResolvedValue({
      connectors: [localConnector],
    });
    act(() => {
      useRegistryStore
        .getState()
        .registerSlot("resource.connector.detail.actions", {
          id: "test-organization-actions",
          component: ({ resource }) => (
            <button type="button">
              Share {String((resource as ConnectorItem).slug)}
            </button>
          ),
        });
    });

    render(
      <ConnectorsPane query="" addMode={null} onAddModeChange={vi.fn()} />,
    );

    expect(
      await screen.findByRole("button", { name: "Share local-search" }),
    ).toBeTruthy();
  });

  it("renders the overlay download action for an organization MCP", async () => {
    act(() => {
      useRegistryStore.getState().registerSlot("resource.connector.actions", {
        id: "test-mcp-download",
        component: ({ resource }) => (
          <button type="button">
            Download {String((resource as ConnectorItem).slug)}
          </button>
        ),
      });
    });

    render(
      <ConnectorsPane query="" addMode={null} onAddModeChange={vi.fn()} />,
    );

    expect(
      await screen.findByRole("button", { name: "Download team-search" }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });

  it("selects a cloud-only organization MCP and keeps its detail inline", async () => {
    act(() => {
      useRegistryStore.getState().registerSlot("resource.connector.actions", {
        id: "test-mcp-download",
        component: ({ resource }) => (
          <button type="button">
            Download {String((resource as ConnectorItem).slug)}
          </button>
        ),
      });
    });

    render(
      <ConnectorsPane query="" addMode={null} onAddModeChange={vi.fn()} />,
    );

    const connectorRow = await screen.findByRole("button", {
      name: /Team Search/,
    });
    fireEvent.click(connectorRow);

    await waitFor(() => {
      expect(connectorRow.className).toContain("bg-surface-soft");
    });
    expect(
      screen.getAllByRole("button", { name: "Download team-search" }),
    ).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
  });

  it("deletes only the downloaded copy and exposes the org download again", async () => {
    vi.mocked(connectorsApi.list)
      .mockResolvedValueOnce({
        connectors: [
          downloadedOrganizationConnector,
          personalBuiltinConnector,
        ],
      })
      .mockResolvedValue({
        connectors: [organizationConnector, personalBuiltinConnector],
      });
    const deleteConnector = vi
      .spyOn(connectorsApi, "delete")
      .mockResolvedValue({ ok: true });

    act(() => {
      useCategoryRegistry.getState().inject("connector", [
        {
          id: "team",
          label: "Organization",
          order: -1,
          multiAssign: true,
          filter: (entry) =>
            (
              (entry as { item?: ConnectorItem }).item as unknown as {
                _sync?: { scope?: string };
              }
            )?._sync?.scope === "org",
        },
      ]);
      useRegistryStore.getState().registerSlot("resource.connector.actions", {
        id: "test-mcp-download",
        component: ({ resource }) =>
          (
            resource as unknown as {
              _sync?: { status?: string };
            }
          )._sync?.status === "cloud_only" ? (
            <button type="button">
              Download {String((resource as ConnectorItem).slug)}
            </button>
          ) : null,
      });
    });

    render(
      <ConnectorsPane query="" addMode={null} onAddModeChange={vi.fn()} />,
    );

    const localDeleteButtons = await screen.findAllByRole("button", {
      name: "Delete",
    });
    expect(localDeleteButtons).toHaveLength(2);
    fireEvent.click(localDeleteButtons[0]);
    const deleteButtons = await screen.findAllByRole("button", {
      name: "Delete",
    });
    fireEvent.click(deleteButtons.at(-1)!);

    await waitFor(() => {
      expect(deleteConnector).toHaveBeenCalledWith("local-connector-1");
    });
    expect(
      await screen.findByRole("button", { name: "Download team-search" }),
    ).toBeTruthy();
    const organizationSection = screen
      .getByText("Organization")
      .closest<HTMLDivElement>("div.mb-8");
    const installedSection = screen
      .getByText("Added")
      .closest<HTMLDivElement>("div.mb-8");
    expect(organizationSection).not.toBeNull();
    expect(installedSection).not.toBeNull();
    expect(screen.getAllByText("Team Search")).toHaveLength(1);
    expect(organizationSection!.textContent).toContain("Team Search");
    expect(installedSection!.textContent).not.toContain("Team Search");
  });
});

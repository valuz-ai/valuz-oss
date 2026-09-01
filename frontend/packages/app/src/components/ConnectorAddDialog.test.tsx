/**
 * Edit mode of the connector dialog.
 *
 * The load-bearing case is `env`. The API never returns a connector's
 * environment variables — they are not on `ConnectorItem` at all — so the
 * form cannot show them, and a dialog that helpfully PATCHed whatever the
 * (empty) editor contained would erase every variable the connector runs on.
 * Blank has to mean "leave as is". These pin that, and the command-line
 * round-trip that decides what the user sees when the form opens.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { initI18n } from "@valuz/shared/i18n";
import type { ConnectorItem, UpdateConnectorRequest } from "@valuz/core";
import { ConnectorAddDialog } from "./ConnectorAddDialog";

const stdioConnector = (
  overrides: Partial<ConnectorItem> = {},
): ConnectorItem =>
  ({
    id: "c1",
    slug: "yearning",
    display_name: "Yearning SQL",
    description: null,
    connector_type: "custom",
    transport: "stdio",
    url: null,
    auth_type: "none",
    has_api_key: false,
    command: "npx",
    args: ["-y", "yearning-mcp@latest"],
    working_dir: null,
    headers: [],
    params: [],
    enabled: true,
    status: "connected",
    tool_count: 3,
    last_tested_at: null,
    error_message: null,
    created_at: 0,
    updated_at: 0,
    ...overrides,
  }) as ConnectorItem;

function renderEdit(connector: ConnectorItem) {
  const onUpdate = vi.fn<(p: UpdateConnectorRequest) => Promise<void>>(
    async () => {},
  );
  render(
    <ConnectorAddDialog
      open
      mode="stdio"
      onOpenChange={() => {}}
      onSubmit={async () => {}}
      initial={connector}
      onUpdate={onUpdate}
    />,
  );
  return onUpdate;
}

const save = () => screen.getByRole("button", { name: /^(save|保存)$/i });

beforeEach(() => {
  initI18n({ locale: "zh-CN", fallbackLocale: "en-US" });
  vi.clearAllMocks();
});

describe("ConnectorAddDialog in edit mode", () => {
  it("shows command and args as one command line the user can edit", () => {
    renderEdit(stdioConnector());

    expect(screen.getByDisplayValue("npx -y yearning-mcp@latest")).toBeTruthy();
  });

  it("quotes an argument that contains a space so the round-trip survives", () => {
    renderEdit(stdioConnector({ args: ["--root", "/My Files"] }));

    expect(screen.getByDisplayValue('npx --root "/My Files"')).toBeTruthy();
  });

  it("omits env entirely when the user leaves the editor untouched", async () => {
    const onUpdate = renderEdit(stdioConnector());

    fireEvent.click(save());

    await waitFor(() => expect(onUpdate).toHaveBeenCalled());
    const payload = onUpdate.mock.calls[0]![0];
    // Not `env: {}` and not `env: null` — the key must be absent, or the
    // backend would replace the connector's variables with nothing.
    expect("env" in payload).toBe(false);
    expect(payload.display_name).toBe("Yearning SQL");
    expect(payload.command).toBe("npx -y yearning-mcp@latest");
  });

  it("sends the whole env set once the user fills any row", async () => {
    const onUpdate = renderEdit(stdioConnector());

    fireEvent.click(screen.getByRole("button", { name: /添加|add/i }));
    const textInputs = screen
      .getAllByRole("textbox")
      .filter((el) => (el as HTMLInputElement).type !== "hidden");
    fireEvent.change(textInputs[textInputs.length - 2]!, {
      target: { value: "YEARNING_ENDPOINT" },
    });
    fireEvent.change(textInputs[textInputs.length - 1]!, {
      target: { value: "https://example.test" },
    });
    fireEvent.click(save());

    await waitFor(() => expect(onUpdate).toHaveBeenCalled());
    expect(onUpdate.mock.calls[0]![0].env).toEqual({
      YEARNING_ENDPOINT: "https://example.test",
    });
  });
});

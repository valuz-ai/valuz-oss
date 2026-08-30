import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { initI18n } from "@valuz/shared/i18n";
import { connectorsApi, marketplaceApi } from "@valuz/core";
import type { MarketplaceItem, MarketplaceItemDetail } from "@valuz/core";
import { MarketplaceConnectorDialog } from "./MarketplaceConnectorDialog";

const connectorItem: MarketplaceItem = {
  id: "market:connector:snowball-securities",
  type: "connector",
  source: "valuz_official",
  source_ref: "snowball-securities",
  title: "Snowball Securities",
  description: "Brokerage connector",
  subcategories: ["brokerage"],
  badges: [],
  stats: {},
  install_target: "connector_library",
  installed: false,
};

function connectorDetail(
  overrides: Partial<MarketplaceItemDetail> = {},
): MarketplaceItemDetail {
  return {
    ...connectorItem,
    origin_url: "https://www.snowballsecurities.com/",
    connector_config: {
      slug: "snowball-securities",
      transport: "http",
      url: "https://openapi.snbsecurities.com/mcp",
      args: [],
      env: {},
      headers: {},
      params: {},
      auth_type: "oauth",
      oauth_authorization_endpoint:
        "https://api.ibkr.com/oauth2/authorize",
      oauth_token_endpoint: "https://api.ibkr.com/oauth2/api/v1/token",
      oauth_registration_endpoint: "https://api.ibkr.com/oauth2/register",
      oauth_scopes: ["mcp.read", "mcp.write"],
      fields: [],
      supported: true,
    },
    ...overrides,
  };
}

function renderDialog() {
  return render(
    <MemoryRouter>
      <MarketplaceConnectorDialog
        item={connectorItem}
        open
        onOpenChange={vi.fn()}
        onConnected={vi.fn()}
      />
    </MemoryRouter>,
  );
}

describe("MarketplaceConnectorDialog provenance", () => {
  beforeEach(() => {
    initI18n({ locale: "zh-CN", fallbackLocale: "en-US" });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows an official source and official detail link for curated connectors", async () => {
    vi.spyOn(marketplaceApi, "get").mockResolvedValue(connectorDetail());

    renderDialog();

    expect(await screen.findByText("Valuz 官方")).toBeTruthy();
    const link = screen.getByRole("link", { name: "查看官方详情" });
    expect(link.getAttribute("href")).toBe(
      "https://www.snowballsecurities.com/",
    );
    expect(screen.queryByText("ModelScope")).toBeNull();
    expect(screen.queryByText("在 ModelScope 查看详情")).toBeNull();
  });

  it("keeps the ModelScope label for ModelScope connectors", async () => {
    vi.spyOn(marketplaceApi, "get").mockResolvedValue(
      connectorDetail({
        source: "modelscope",
        origin_url: "https://modelscope.cn/mcp/servers/example",
      }),
    );

    renderDialog();

    expect(await screen.findByText("ModelScope")).toBeTruthy();
    expect(
      screen
        .getByRole("link", { name: "在 ModelScope 查看详情" })
        .getAttribute("href"),
    ).toBe("https://modelscope.cn/mcp/servers/example");
  });

  it("passes curated OAuth metadata and scopes to connector creation", async () => {
    vi.spyOn(marketplaceApi, "get").mockResolvedValue(connectorDetail());
    const create = vi.spyOn(connectorsApi, "create").mockResolvedValue({
      id: "connector-1",
      slug: "snowball-securities",
      needs_auth: false,
      authorization_url: null,
    });

    renderDialog();
    fireEvent.click(
      await screen.findByRole("button", { name: "添加连接器" }),
    );

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          oauth_authorization_endpoint:
            "https://api.ibkr.com/oauth2/authorize",
          oauth_token_endpoint: "https://api.ibkr.com/oauth2/api/v1/token",
          oauth_registration_endpoint: "https://api.ibkr.com/oauth2/register",
          oauth_scopes: ["mcp.read", "mcp.write"],
        }),
      ),
    );
  });
});

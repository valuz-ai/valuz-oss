import { describe, expect, it } from "vitest";
import type { ConnectorItem } from "@valuz/core";
import { reauthorizePayload, shouldReauthorize } from "./connector-reconnect";

function makeConnector(overrides: Partial<ConnectorItem> = {}): ConnectorItem {
  return {
    id: "c1",
    slug: "valuz-search",
    display_name: "Valuz · Search",
    description: "Full-market search",
    connector_type: "builtin",
    transport: "http",
    url: "https://data.valuz.cn/mcp/search",
    auth_type: "none",
    has_api_key: false,
    command: null,
    args: [],
    working_dir: null,
    env: {},
    headers: [],
    params: [],
    enabled: true,
    status: "error",
    tool_count: null,
    last_tested_at: null,
    error_message: "Client error '401 Unauthorized'",
    created_at: 0,
    updated_at: 0,
    ...overrides,
  };
}

describe("shouldReauthorize", () => {
  it("escalates a failed re-probe to re-authorization for OAuth connectors", () => {
    expect(shouldReauthorize(makeConnector({ auth_type: "oauth" }))).toBe(true);
  });

  it("does not re-authorize a non-OAuth connector", () => {
    expect(shouldReauthorize(makeConnector({ auth_type: "none" }))).toBe(false);
  });
});

describe("reauthorizePayload", () => {
  it("mirrors the field-less catalog connect payload", () => {
    expect(reauthorizePayload(makeConnector({ auth_type: "oauth" }))).toEqual({
      slug: "valuz-search",
      display_name: "Valuz · Search",
      transport: "http",
      url: "https://data.valuz.cn/mcp/search",
      auth_type: "oauth",
      description: "Full-market search",
      connector_type: "builtin",
    });
  });

  it("falls back to http transport when the connector has none recorded", () => {
    expect(reauthorizePayload(makeConnector({ transport: "" })).transport).toBe(
      "http",
    );
  });
});

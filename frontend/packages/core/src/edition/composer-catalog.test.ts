import { afterEach, describe, expect, it, vi } from "vitest";

import { agentsApi, type Agent } from "../api/agents-api";
import { providersApi, type LLMChannel } from "../api/providers-api";
import {
  getComposerCatalogAdapter,
  setComposerCatalogAdapter,
  type ComposerCatalogAdapter,
} from "./composer-catalog";

afterEach(() => {
  setComposerCatalogAdapter(null);
  vi.restoreAllMocks();
});

describe("composer catalog adapter", () => {
  it("uses the module-default APIs when no edition adapter is installed", async () => {
    const agents = [] as Agent[];
    const providers = [] as LLMChannel[];
    const listAgents = vi
      .spyOn(agentsApi, "listAgents")
      .mockResolvedValue({ agents });
    const listProviders = vi
      .spyOn(providersApi, "list")
      .mockResolvedValue({ providers });

    const adapter = getComposerCatalogAdapter();
    const context = { targetId: "opaque-commercial-target" };

    expect(adapter.getScopeKey(context)).toBe("oss-default");
    await expect(adapter.listAgents(context)).resolves.toEqual({ agents });
    await expect(adapter.listProviderChannels(context)).resolves.toEqual({
      providers,
    });
    expect(listAgents).toHaveBeenCalledWith(undefined, { fresh: true });
    expect(listProviders).toHaveBeenCalledWith({ gated: true, fresh: true });
  });

  it("returns the edition adapter unchanged once installed", () => {
    const adapter: ComposerCatalogAdapter = {
      getScopeKey: ({ targetId }) => `custom:${targetId ?? "default"}`,
      listAgents: vi.fn(),
      listProviderChannels: vi.fn(),
    };

    setComposerCatalogAdapter(adapter);

    expect(getComposerCatalogAdapter()).toBe(adapter);
    expect(adapter.getScopeKey({ targetId: "cloud" })).toBe("custom:cloud");
  });
});

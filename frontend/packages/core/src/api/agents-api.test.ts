import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { agentsApi } from "./agents-api";
import { invalidateRequestCache } from "./request";
import { setExecutionTargets } from "../edition/execution-targets";

const LOCAL = { id: "local", labelKey: "local", baseUrl: "", isDefault: true };
const CLOUD = { id: "cloud", labelKey: "cloud", baseUrl: "https://cloud.test" };
const DEVICE = {
  id: "device:d1",
  labelKey: "d1",
  baseUrl: "https://relay.test/proxy",
};

function answerWith(agents: Record<string, string[]>): typeof fetch {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const base = Object.keys(agents).find((b) => url.startsWith(b)) ?? "";
    return new Response(
      JSON.stringify({
        agents: (agents[base] ?? []).map((slug) => ({ slug, name: slug })),
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    );
  }) as unknown as typeof fetch;
}

beforeEach(() => {
  setExecutionTargets([]);
  // The list is cached per URL for 30s — without this, one case's answers
  // leak into the next one's fan-out.
  invalidateRequestCache({ tags: ["agents"] });
});

afterEach(() => {
  setExecutionTargets([]);
  vi.restoreAllMocks();
});

describe("agent library fan-out", () => {
  it("asks every machine and tags the rows that came from elsewhere", async () => {
    // Two machines, two libraries: the union is what you may run, and a row
    // has to carry the machine so the composer can follow it there.
    setExecutionTargets([LOCAL, DEVICE]);
    vi.stubGlobal(
      "fetch",
      answerWith({ "https://relay.test/proxy": ["sde", "writer"], "": ["sde"] }),
    );

    const { agents } = await agentsApi.listAgents();

    expect(agents.map((a) => [a.slug, a.exec_target_id])).toEqual([
      // Same slug on both machines is NOT deduped: different instructions,
      // different agent.
      ["sde", undefined],
      ["sde", "device:d1"],
      ["writer", "device:d1"],
    ]);
  });

  it("skips a sibling runtime of the same account", async () => {
    // The cloud execution plane materializes THIS account's library. Fanning
    // out to it lists every agent a second time instead of finding new ones —
    // which is what made every row appear in triplicate.
    setExecutionTargets([LOCAL, CLOUD, DEVICE]);
    const fetchMock = answerWith({
      "https://cloud.test": ["sde"],
      "https://relay.test/proxy": ["sde"],
      "": ["sde"],
    });
    vi.stubGlobal("fetch", fetchMock);

    const { agents } = await agentsApi.listAgents();

    expect(agents.map((a) => a.exec_target_id)).toEqual([
      undefined,
      "device:d1",
    ]);
    const asked = (fetchMock as unknown as { mock: { calls: unknown[][] } }).mock
      .calls.map((c) => String(c[0]));
    expect(asked.some((u) => u.startsWith("https://cloud.test"))).toBe(false);
  });

  it("addressing one machine explicitly opts out of the fan-out", async () => {
    // How the fan-out itself asks, and how the composer reads a single
    // machine's library.
    setExecutionTargets([LOCAL, DEVICE]);
    vi.stubGlobal(
      "fetch",
      answerWith({ "https://relay.test/proxy": ["sde", "writer"] }),
    );

    const { agents } = await agentsApi.listAgents(undefined, {
      baseUrl: "https://relay.test/proxy",
      fresh: true,
    });

    expect(agents.map((a) => a.slug)).toEqual(["sde", "writer"]);
    expect(agents.every((a) => a.exec_target_id === undefined)).toBe(true);
  });
});

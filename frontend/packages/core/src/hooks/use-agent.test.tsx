/** @vitest-environment jsdom */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Agent } from "../api/agents-api";
import { clearRequestCacheForTests } from "../api/request";
import { setComposerCatalogAdapter } from "../edition/composer-catalog";
import { useComposerAgentLibrary } from "./use-agent";

const agent = (slug: string): Agent => ({
  id: slug,
  slug,
  name: slug,
  description: "",
  instructions: "",
  runtime: "claude_agent",
  model: "claude-sonnet-4-6",
  skills: [],
  connector_types: [],
  knowledge_scope: [],
  provider_id: null,
  effort: null,
  kind: "standard",
  resource_policy: "explicit",
  inherit_global_instructions: true,
  permission_mode: "full_access",
  source: "custom",
  readonly: false,
  deletable: true,
  avatar: null,
});

afterEach(() => {
  setComposerCatalogAdapter(null);
  clearRequestCacheForTests();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useComposerAgentLibrary", () => {
  it("reloads agents from each selected execution target", async () => {
    const listAgents = vi.fn(({ targetId }: { targetId?: string | null }) =>
      Promise.resolve({
        agents: [
          targetId === "cloud" ? agent("cloud-agent") : agent("local-agent"),
        ],
      }),
    );
    setComposerCatalogAdapter({
      getScopeKey: ({ targetId }) => `test:${targetId ?? "default"}`,
      listAgents,
      listProviderChannels: vi.fn(),
    });

    const { result, rerender } = renderHook(
      ({ targetId }) => useComposerAgentLibrary(targetId),
      { initialProps: { targetId: "local" } },
    );

    await waitFor(() =>
      expect(result.current).toMatchObject({
        agents: [agent("local-agent")],
        loaded: true,
        failed: false,
        settling: false,
      }),
    );

    rerender({ targetId: "cloud" });
    expect(result.current).toMatchObject({
      agents: [],
      loaded: false,
      failed: false,
      settling: false,
    });
    await waitFor(() =>
      expect(result.current).toMatchObject({
        agents: [agent("cloud-agent")],
        loaded: true,
        failed: false,
        settling: false,
      }),
    );

    expect(listAgents).toHaveBeenCalledTimes(2);
    expect(listAgents.mock.calls.map(([context]) => context.targetId)).toEqual([
      "local",
      "cloud",
    ]);
  });

  it("keeps asking while the roster is empty, so a not-yet-seeded library is not reported as none", async () => {
    // A fresh install seeds its built-in agent server-side after login; the
    // first answer here is legitimately empty and must not settle as "loaded".
    vi.useFakeTimers();
    const listAgents = vi
      .fn()
      .mockResolvedValueOnce({ agents: [] })
      .mockResolvedValue({ agents: [agent("valuz-helper")] });
    setComposerCatalogAdapter({
      getScopeKey: () => "test:local",
      listAgents,
      listProviderChannels: vi.fn(),
    });

    const { result } = renderHook(() => useComposerAgentLibrary("local"));

    await act(async () => {});
    // A response arrived (pickers can render) but it is still being re-asked,
    // so a caller must not claim "nothing configured" yet.
    expect(result.current.agents).toEqual([]);
    expect(result.current.loaded).toBe(true);
    expect(result.current.settling).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(result.current).toMatchObject({
      agents: [agent("valuz-helper")],
      loaded: true,
      failed: false,
      settling: false,
    });
    expect(listAgents).toHaveBeenCalledTimes(2);
  });

  it("stops retrying and reports the failure instead of an empty roster", async () => {
    vi.useFakeTimers();
    const EXPECTED_ATTEMPTS = 4;
    const listAgents = vi.fn().mockRejectedValue(new Error("offline"));
    setComposerCatalogAdapter({
      getScopeKey: () => "test:local",
      listAgents,
      listProviderChannels: vi.fn(),
    });

    const { result } = renderHook(() => useComposerAgentLibrary("local"));

    for (let round = 0; round < EXPECTED_ATTEMPTS; round += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });
    }

    // ``failed`` is what lets a caller distinguish "no agents" from "could not
    // ask", and the retries are bounded rather than looping forever.
    expect(result.current).toMatchObject({
      agents: [],
      loaded: true,
      failed: true,
      settling: false,
    });
    expect(listAgents).toHaveBeenCalledTimes(EXPECTED_ATTEMPTS);
  });

  it("re-asks when the window regains focus", async () => {
    const listAgents = vi
      .fn()
      .mockResolvedValueOnce({ agents: [agent("first")] })
      .mockResolvedValue({ agents: [agent("first"), agent("second")] });
    setComposerCatalogAdapter({
      getScopeKey: () => "test:local",
      listAgents,
      listProviderChannels: vi.fn(),
    });

    const { result } = renderHook(() => useComposerAgentLibrary("local"));
    await waitFor(() => expect(result.current.agents).toHaveLength(1));

    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });

    await waitFor(() => expect(result.current.agents).toHaveLength(2));
  });

  it("ignores an obsolete response after switching targets", async () => {
    let resolveLocal!: (value: Response) => void;
    let resolveCloud!: (value: Response) => void;
    const localRequest = new Promise<Response>((resolve) => {
      resolveLocal = resolve;
    });
    const cloudRequest = new Promise<Response>((resolve) => {
      resolveCloud = resolve;
    });
    const listAgents = vi
      .fn()
      .mockReturnValueOnce(localRequest.then((response) => response.json()))
      .mockReturnValueOnce(cloudRequest.then((response) => response.json()));
    setComposerCatalogAdapter({
      getScopeKey: ({ targetId }) => `test:${targetId ?? "default"}`,
      listAgents,
      listProviderChannels: vi.fn(),
    });

    const { result, rerender } = renderHook(
      ({ targetId }) => useComposerAgentLibrary(targetId),
      { initialProps: { targetId: "local" } },
    );

    rerender({ targetId: "cloud" });
    expect(result.current).toMatchObject({
      agents: [],
      loaded: false,
      failed: false,
      settling: false,
    });

    await act(async () => {
      resolveLocal(
        new Response(JSON.stringify({ agents: [agent("local-agent")] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
      await localRequest;
    });
    expect(result.current).toMatchObject({
      agents: [],
      loaded: false,
      failed: false,
      settling: false,
    });

    await act(async () => {
      resolveCloud(
        new Response(JSON.stringify({ agents: [agent("cloud-agent")] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
      await cloudRequest;
    });
    expect(result.current).toMatchObject({
      agents: [agent("cloud-agent")],
      loaded: true,
      failed: false,
      settling: false,
    });
  });

  it("reloads the current target when its refresh key changes", async () => {
    const listAgents = vi.fn().mockResolvedValue({ agents: [] });
    setComposerCatalogAdapter({
      getScopeKey: ({ targetId }) => `test:${targetId ?? "default"}`,
      listAgents,
      listProviderChannels: vi.fn(),
    });

    const { result, rerender } = renderHook(
      ({ refreshKey }) =>
        useComposerAgentLibrary("local", refreshKey),
      { initialProps: { refreshKey: "first" } },
    );

    await waitFor(() => expect(result.current.loaded).toBe(true));
    rerender({ refreshKey: "second" });
    expect(result.current).toMatchObject({
      agents: [],
      loaded: false,
      failed: false,
      settling: false,
    });
    await waitFor(() => expect(result.current.loaded).toBe(true));
    // Two scopes were asked; an empty roster is also re-asked, so the floor is
    // what matters here, not the exact count.
    expect(listAgents.mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});

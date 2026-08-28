/** @vitest-environment jsdom */
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SkillView } from "@valuz/core";

const getEffectiveResources = vi.hoisted(() => vi.fn());
vi.mock("@valuz/core", () => ({ agentsApi: { getEffectiveResources } }));

const { useAgentEffectiveSkills } =
  await import("./use-agent-effective-skills");

const manifest = (slugs: string[]) => ({
  policy: "explicit",
  resolved_at: 0,
  counts: { skills: slugs.length, connectors: 0, knowledge_bases: 0 },
  skills: slugs.map((slug) => ({
    id: slug,
    slug,
    name: slug,
    source: "user",
    status: "available",
  })),
  connectors: [],
  knowledge_bases: [],
  warnings: [],
});

const CATALOG = [
  {
    id: "dcf",
    slug: "dcf",
    name: "DCF",
    description: "discounted cash flow",
  } as SkillView,
];

describe("useAgentEffectiveSkills", () => {
  beforeEach(() => {
    getEffectiveResources.mockReset();
  });

  it("lists what the backend says the session will carry", async () => {
    // Including the always-on baseline, which no client-side derivation from
    // ``agent.skills`` could have known about.
    getEffectiveResources.mockResolvedValue(manifest(["dcf", "skill-creator"]));

    const { result } = renderHook(() =>
      useAgentEffectiveSkills("analyst", CATALOG),
    );

    await waitFor(() =>
      expect(result.current.map((i) => i.slug)).toEqual([
        "dcf",
        "skill-creator",
      ]),
    );
  });

  it("joins the catalog for the description the picker searches on", async () => {
    getEffectiveResources.mockResolvedValue(manifest(["dcf"]));

    const { result } = renderHook(() =>
      useAgentEffectiveSkills("analyst", CATALOG),
    );

    await waitFor(() =>
      expect(result.current[0]).toMatchObject({
        name: "DCF",
        description: "discounted cash flow",
      }),
    );
  });

  it("keeps a skill the catalog has never heard of", async () => {
    // The backend is the authority on membership; the catalog is display only.
    getEffectiveResources.mockResolvedValue(manifest(["citation"]));

    const { result } = renderHook(() =>
      useAgentEffectiveSkills("analyst", CATALOG),
    );

    await waitFor(() =>
      expect(result.current.map((i) => i.slug)).toEqual(["citation"]),
    );
  });

  it("asks for nothing when no agent is selected", () => {
    const { result } = renderHook(() => useAgentEffectiveSkills(null, CATALOG));

    expect(result.current).toEqual([]);
    expect(getEffectiveResources).not.toHaveBeenCalled();
  });

  it("shows nothing rather than a guess when the call fails", async () => {
    // A guessed entry inserts a ``/slug`` the runtime may not actually have.
    getEffectiveResources.mockRejectedValue(new Error("offline"));

    const { result } = renderHook(() =>
      useAgentEffectiveSkills("analyst", CATALOG),
    );

    await waitFor(() => expect(getEffectiveResources).toHaveBeenCalled());
    expect(result.current).toEqual([]);
  });

  it("never shows the previous agent's skills after a switch", async () => {
    getEffectiveResources.mockResolvedValue(manifest(["dcf"]));
    const { result, rerender } = renderHook(
      ({ slug }: { slug: string }) => useAgentEffectiveSkills(slug, CATALOG),
      { initialProps: { slug: "analyst" } },
    );
    await waitFor(() => expect(result.current).toHaveLength(1));

    // Next agent's answer has not arrived yet.
    getEffectiveResources.mockReturnValue(new Promise(() => {}));
    rerender({ slug: "modeler" });

    expect(result.current).toEqual([]);
  });

  it("does not refetch when only the catalog identity changes", async () => {
    getEffectiveResources.mockResolvedValue(manifest(["dcf"]));
    const { rerender } = renderHook(
      ({ catalog }: { catalog: SkillView[] }) =>
        useAgentEffectiveSkills("analyst", catalog),
      { initialProps: { catalog: [...CATALOG] } },
    );
    await waitFor(() => expect(getEffectiveResources).toHaveBeenCalledTimes(1));

    rerender({ catalog: [...CATALOG] });
    rerender({ catalog: [...CATALOG] });

    expect(getEffectiveResources).toHaveBeenCalledTimes(1);
  });
});

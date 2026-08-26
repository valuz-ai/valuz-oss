import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { initI18n } from "@valuz/shared/i18n";
import {
  skillsApi,
  useCategoryRegistry,
  useRegistryStore,
} from "@valuz/core";
import type { SkillView } from "@valuz/core";
import { SkillsPane } from "./SkillsPane";

const organizationSkill = {
  id: "org-skill-1",
  slug: "team-research",
  name: "Team Research",
  description: "Shared by the organization",
  tags: [],
  source: "valuz",
  scope: "user",
  path: "",
  enabled: true,
  library_enabled: false,
  deletable: false,
  readonly: true,
  creation_origin: "imported",
  _sync: {
    status: "cloud_only",
    cloud_id: "org-skill-1",
    scope: "org",
  },
} as unknown as SkillView;

describe("SkillsPane extension slots", () => {
  beforeEach(() => {
    initI18n({ locale: "en-US", fallbackLocale: "en-US" });
    vi.spyOn(skillsApi, "list").mockResolvedValue({
      project_id: "chat-default",
      skills: [organizationSkill],
    });
    vi.spyOn(skillsApi, "listFiles").mockResolvedValue([]);
  });

  afterEach(() => {
    act(() => {
      useRegistryStore
        .getState()
        .unregisterSlot("resource.skill.actions", "test-skill-download");
      useRegistryStore
        .getState()
        .unregisterSlot("resource.skill.cloud-detail", "test-cloud-detail");
      useCategoryRegistry.getState().remove("skill");
    });
    vi.restoreAllMocks();
  });

  it("renders the overlay download action for an organization skill", async () => {
    act(() => {
      useRegistryStore.getState().registerSlot("resource.skill.actions", {
        id: "test-skill-download",
        component: ({ resource }) => (
          <button type="button">
            Download {String((resource as SkillView).slug)}
          </button>
        ),
      });
    });

    render(
      <MemoryRouter>
        <SkillsPane query="" addMode={null} onAddModeChange={vi.fn()} />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("button", { name: "Download team-research" }),
    ).toBeTruthy();
  });

  it("uses the skill source for badges inside an injected organization group", async () => {
    const codexOrganizationSkill = {
      ...organizationSkill,
      id: "codex:opscli-agent",
      slug: "opscli-agent",
      name: "OpsCLI Agent",
      source: "codex",
      path: "/Users/member/.codex/skills/opscli-agent",
      _sync: undefined,
      _org_sync: {
        status: "synced",
        cloud_id: "org-opscli-agent",
        scope: "org",
      },
    } as unknown as SkillView;
    vi.mocked(skillsApi.list).mockResolvedValue({
      project_id: "chat-default",
      skills: [codexOrganizationSkill],
    });
    act(() => {
      useCategoryRegistry.getState().inject("skill", [
        {
          id: "team",
          label: "Organization",
          order: -1,
          multiAssign: true,
          filter: (item) =>
            (item as SkillView & {
              _org_sync?: { scope?: string };
            })._org_sync?.scope === "org",
        },
      ]);
    });

    render(
      <MemoryRouter>
        <SkillsPane query="" addMode={null} onAddModeChange={vi.fn()} />
      </MemoryRouter>,
    );

    const organizationHeading = await screen.findByText("Organization");
    const organizationSection =
      organizationHeading.closest<HTMLDivElement>("div.mb-8");
    expect(organizationSection).not.toBeNull();
    expect(within(organizationSection!).getByText("Codex")).toBeTruthy();
    expect(within(organizationSection!).queryByText("Claude")).toBeNull();
  });

  it("selects a cloud-only organization skill and renders its detail inline", async () => {
    const localSkill = {
      ...organizationSkill,
      id: "local-skill",
      slug: "local-skill",
      name: "Local Skill",
      path: "/tmp/local-skill",
      readonly: false,
      deletable: true,
      _sync: undefined,
    } as unknown as SkillView;
    vi.mocked(skillsApi.list).mockResolvedValue({
      project_id: "chat-default",
      skills: [localSkill, organizationSkill],
    });
    act(() => {
      useRegistryStore.getState().registerSlot("resource.skill.cloud-detail", {
        id: "test-cloud-detail",
        component: ({ resource }) => (
          <div>Cloud detail {String((resource as SkillView).name)}</div>
        ),
      });
    });

    render(
      <MemoryRouter>
        <SkillsPane query="" addMode={null} onAddModeChange={vi.fn()} />
      </MemoryRouter>,
    );

    const organizationName = await screen.findByText("Team Research");
    fireEvent.click(organizationName);

    expect(await screen.findByText("Cloud detail Team Research")).toBeTruthy();
    await waitFor(() => {
      expect(organizationName.closest(".border-brand")).not.toBeNull();
    });
    expect(skillsApi.listFiles).not.toHaveBeenCalledWith("org-skill-1");
  });

  it("uses organization sync state for the organization copy of a local skill", async () => {
    const syncedBothSkill = {
      ...organizationSkill,
      id: "agents:shared-skill",
      slug: "shared-skill",
      name: "Shared Skill",
      path: "/tmp/shared-skill",
      readonly: false,
      _sync: { status: "local_only", cloud_id: null, scope: null },
      _org_sync: {
        status: "synced",
        cloud_id: "org-shared-skill",
        scope: "org",
      },
    } as unknown as SkillView;
    vi.mocked(skillsApi.list).mockResolvedValue({
      project_id: "chat-default",
      skills: [syncedBothSkill],
    });
    act(() => {
      useCategoryRegistry.getState().inject("skill", [
        {
          id: "team",
          label: "Organization",
          order: -1,
          multiAssign: true,
          filter: (item) =>
            Boolean(
              (item as SkillView & { _org_sync?: { scope?: string } })
                ._org_sync,
            ),
          groupBy: () => "org-shared-skill",
        },
      ]);
      useRegistryStore.getState().registerSlot("resource.skill.actions", {
        id: "test-skill-download",
        component: ({ resource }) => (
          <span>
            Sync state{" "}
            {String(
              (resource as { _sync?: { status?: string } })._sync?.status,
            )}
          </span>
        ),
      });
    });

    render(
      <MemoryRouter>
        <SkillsPane query="" addMode={null} onAddModeChange={vi.fn()} />
      </MemoryRouter>,
    );

    const organizationSection = (
      await screen.findByText("Organization")
    ).closest<HTMLDivElement>("div.mb-8");
    const agentsSection = screen.getByText("Agents").closest<HTMLDivElement>(
      "div.mb-8",
    );
    expect(organizationSection).not.toBeNull();
    expect(agentsSection).not.toBeNull();
    expect(
      within(organizationSection!).getByText("Sync state synced"),
    ).toBeTruthy();
    expect(
      within(agentsSection!).getByText("Sync state local_only"),
    ).toBeTruthy();
  });
});

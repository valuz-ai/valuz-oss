import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SkillStagingPanel, type StagingPanelSlug } from "./SkillStagingPanel";

const baseProps = {
  refreshing: false,
  syncing: false,
  onRefresh: vi.fn(),
  onSync: vi.fn(),
};

const buildSlug = (overrides: Partial<StagingPanelSlug>): StagingPanelSlug => ({
  slug: "weekly-report",
  name: "weekly-report",
  description: "Weekly report template.",
  fileCount: 3,
  totalBytes: 2048,
  conflictKind: "none",
  suggestedStrategy: "overwrite",
  ...overrides,
});

describe("SkillStagingPanel", () => {
  it("should show the empty state when no slugs have been generated", () => {
    render(<SkillStagingPanel {...baseProps} slugs={[]} />);
    expect(screen.getByText("还没有生成的技能。")).toBeTruthy();
  });

  it("should render the slug name, version badge, and conflict label", () => {
    const slug = buildSlug({
      version: 3,
      conflictKind: "diverged",
      suggestedStrategy: "fork",
      suggestedNewSlug: "weekly-report-v4",
    });
    render(<SkillStagingPanel {...baseProps} slugs={[slug]} />);

    expect(screen.getByText("weekly-report")).toBeTruthy();
    expect(screen.getByText("v3")).toBeTruthy();
    expect(screen.getByText("原技能已变化")).toBeTruthy();
  });

  it("should pass the suggested strategy through to onSync", () => {
    const onSync = vi.fn();
    const slug = buildSlug({
      conflictKind: "diverged",
      suggestedStrategy: "fork",
      suggestedNewSlug: "weekly-report-v2",
    });
    render(<SkillStagingPanel {...baseProps} slugs={[slug]} onSync={onSync} />);

    fireEvent.click(screen.getByText(/同步 1 个到技能库/));

    expect(onSync).toHaveBeenCalledWith([
      { slug: "weekly-report", strategy: "fork", newSlug: "weekly-report-v2" },
    ]);
  });

  it("should default to overwrite strategy when there is no conflict", () => {
    const onSync = vi.fn();
    const slug = buildSlug({});
    render(<SkillStagingPanel {...baseProps} slugs={[slug]} onSync={onSync} />);

    fireEvent.click(screen.getByText(/同步 1 个到技能库/));

    expect(onSync).toHaveBeenCalledWith([
      { slug: "weekly-report", strategy: "overwrite" },
    ]);
  });

  it("should skip slugs the user unchecked", () => {
    const onSync = vi.fn();
    const slugs = [
      buildSlug({ slug: "a", name: "a" }),
      buildSlug({ slug: "b", name: "b" }),
    ];
    render(<SkillStagingPanel {...baseProps} slugs={slugs} onSync={onSync} />);

    const checkboxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    fireEvent.click(checkboxes[0]); // uncheck 'a'

    fireEvent.click(screen.getByText(/同步 1 个到技能库/));
    expect(onSync).toHaveBeenCalledWith([{ slug: "b", strategy: "overwrite" }]);
  });

  it("should disable the sync button while a sync is in flight", () => {
    const slug = buildSlug({});
    render(<SkillStagingPanel {...baseProps} slugs={[slug]} syncing />);
    const btn = screen
      .getByText(/同步中…/)
      .closest("button") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("should call onRefresh when the refresh button is clicked", () => {
    const onRefresh = vi.fn();
    render(
      <SkillStagingPanel {...baseProps} slugs={[]} onRefresh={onRefresh} />,
    );
    fireEvent.click(screen.getByLabelText("刷新"));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("should let the user override the fork slug name", () => {
    const onSync = vi.fn();
    const slug = buildSlug({
      conflictKind: "diverged",
      suggestedStrategy: "fork",
      suggestedNewSlug: "weekly-report-v2",
    });
    render(<SkillStagingPanel {...baseProps} slugs={[slug]} onSync={onSync} />);

    const newSlugInput = screen.getByDisplayValue("weekly-report-v2");
    fireEvent.change(newSlugInput, {
      target: { value: "weekly-report-experimental" },
    });

    fireEvent.click(screen.getByText(/同步 1 个到技能库/));
    expect(onSync).toHaveBeenCalledWith([
      {
        slug: "weekly-report",
        strategy: "fork",
        newSlug: "weekly-report-experimental",
      },
    ]);
  });
});

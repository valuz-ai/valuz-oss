/** @vitest-environment jsdom */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("../conversation/MarkdownContent", () => ({
  MarkdownContent: ({ content }: { content: string }) => (
    <pre data-testid="markdown-content">{content}</pre>
  ),
}));

import { t } from "@valuz/shared/i18n";

import {
  SkillDetailPanel,
  type SkillDetailPanelFile,
} from "./SkillDetailPanel";

const skill = (name: string) => ({
  name,
  description: `${name} description`,
  tags: [],
  source: "custom" as const,
  version: "v1",
  category: "agents" as const,
});

const files: SkillDetailPanelFile[] = [
  {
    path: "SKILL.md",
    type: "file",
    size: null,
  },
];

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe("SkillDetailPanel", () => {
  beforeAll(() => {
    if (!globalThis.ResizeObserver) {
      globalThis.ResizeObserver = class ResizeObserver {
        observe() {}
        unobserve() {}
        disconnect() {}
      };
    }
  });

  it("renders edition-provided header actions", () => {
    render(
      <SkillDetailPanel
        skill={skill("Shared")}
        files={[]}
        headerActions={<button type="button">Publish</button>}
      />,
    );

    expect(screen.getByRole("button", { name: "Publish" })).toBeTruthy();
  });

  it("keeps native copy as the first menu action when editions contribute", async () => {
    const onCopy = vi.fn();
    const user = userEvent.setup();
    render(
      <SkillDetailPanel
        skill={skill("Shared")}
        files={[]}
        onCopy={onCopy}
        copyMenuItems={<div role="menuitem">Copy to organizations</div>}
      />,
    );

    await user.click(screen.getByRole("button"));
    const items = screen.getAllByRole("menuitem");
    expect(items[1]?.textContent).toContain("Copy to organizations");
    fireEvent.click(items[0]);
    expect(onCopy).toHaveBeenCalledTimes(1);
  });

  it("clears previous SKILL.md content while the next skill loads", async () => {
    const loadFirst = vi.fn().mockResolvedValue(`---
name: first-skill
description: first description
---

# First`);
    const nextContent = deferred<string>();
    const loadSecond = vi.fn().mockReturnValue(nextContent.promise);

    const { rerender } = render(
      <SkillDetailPanel
        skill={skill("First")}
        files={files}
        onLoadFile={loadFirst}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("markdown-content").textContent).toContain(
        "first-skill",
      );
    });

    rerender(
      <SkillDetailPanel
        skill={skill("Second")}
        files={files}
        onLoadFile={loadSecond}
      />,
    );

    expect(loadSecond).toHaveBeenCalledWith("SKILL.md");
    expect(screen.queryByText(/first-skill/)).toBeNull();

    nextContent.resolve(`---
name: second-skill
description: second description
---

# Second`);

    await waitFor(() => {
      expect(screen.getByTestId("markdown-content").textContent).toContain(
        "second-skill",
      );
    });
  });

  it("keeps the preview when only the loader identity changes", async () => {
    // Callers pass ``onLoadFile`` as an inline arrow, so it is a fresh closure
    // on every parent render — and the app layout re-renders every page under
    // it on a background poll. Refetching on that identity blanked the preview
    // on a timer (a visible flash every few seconds). Assert the loader is
    // called ONCE, not merely that the content ends up right: a blank-then-
    // refetch cycle also ends up right.
    const load = vi.fn().mockResolvedValue(`---
name: stable-skill
description: stable description
---

# Stable`);

    const { rerender } = render(
      <SkillDetailPanel
        skill={skill("Stable")}
        files={files}
        onLoadFile={(path) => load(path)}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("markdown-content").textContent).toContain(
        "stable-skill",
      );
    });

    // Same skill, same file tree — only the closure is new.
    rerender(
      <SkillDetailPanel
        skill={skill("Stable")}
        files={files}
        onLoadFile={(path) => load(path)}
      />,
    );

    expect(load).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("markdown-content").textContent).toContain(
      "stable-skill",
    );
  });

  it("says the files are withheld, not missing, for a protected skill", () => {
    // "无文件" would read as a broken load and send people hunting for a bug.
    // There ARE files; the panel is declining to list them.
    render(
      <SkillDetailPanel
        skill={{ ...skill("Guarded"), protected: true }}
        files={[]}
      />,
    );

    expect(screen.queryByText(t("skill.noFiles"))).toBeNull();
    expect(screen.queryByText(t("skill.selectFileToPreview"))).toBeNull();
    expect(
      screen.getAllByText(t("skill.protectedBadge")).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText(t("skill.protectedFilesHidden"))).toBeTruthy();
  });

  it("still shows an unprotected skill its file tree and preview prompt", () => {
    render(<SkillDetailPanel skill={skill("Open")} files={[]} />);

    expect(screen.getByText(t("skill.noFiles"))).toBeTruthy();
    expect(screen.getByText(t("skill.selectFileToPreview"))).toBeTruthy();
    expect(screen.queryByText(t("skill.protectedFilesHidden"))).toBeNull();
  });
});

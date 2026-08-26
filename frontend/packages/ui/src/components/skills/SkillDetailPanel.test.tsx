/** @vitest-environment jsdom */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("../conversation/MarkdownContent", () => ({
  MarkdownContent: ({ content }: { content: string }) => (
    <pre data-testid="markdown-content">{content}</pre>
  ),
}));

import { SkillDetailPanel, type SkillDetailPanelFile } from "./SkillDetailPanel";

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
});

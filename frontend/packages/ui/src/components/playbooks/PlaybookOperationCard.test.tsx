import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PlaybookOperationCard } from "./PlaybookOperationCard";

vi.mock("../../hooks/use-i18n", () => ({
  useI18n: () => ({ t: (key: string) => key }),
}));

const proposedOperation = {
  state: "proposed",
  preview: {
    change: "create",
    name: "半导体景气跟踪",
    content: "# 核心观点\n\n- 跟踪 HBM 需求\n- 检查 CoWoS 供给",
  },
  result_payload: {},
  error_message: null,
};

describe("PlaybookOperationCard", () => {
  it("opens the full Prompt from a compact icon and renders Markdown", async () => {
    render(
      <PlaybookOperationCard
        operation={proposedOperation}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    const details = screen.getByRole("button", {
      name: "playbook.promptLabel",
    });
    expect(details.getAttribute("data-size")).toBe("icon-xs");
    expect(details.textContent).toBe("");

    await userEvent.click(details);

    expect(
      screen.getByRole("heading", { name: "半导体景气跟踪" }),
    ).toBeTruthy();
    expect(screen.getByRole("heading", { name: "核心观点" })).toBeTruthy();
    expect(screen.getByRole("list")).toBeTruthy();
  });

  it("uses the standard action hierarchy and semantic success surface", () => {
    const { container, rerender } = render(
      <PlaybookOperationCard
        operation={proposedOperation}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("button", { name: "common.cancel" }).getAttribute(
        "data-variant",
      ),
    ).toBe("outline");
    expect(
      screen.getByRole("button", { name: "common.confirm" }).getAttribute(
        "data-variant",
      ),
    ).toBe("default");

    rerender(
      <PlaybookOperationCard
        operation={{ ...proposedOperation, state: "succeeded" }}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    const card = container.querySelector('[data-slot="playbook-operation-card"]');
    expect(card?.classList.contains("border-success/40")).toBe(true);
    expect(card?.classList.contains("bg-success/5")).toBe(true);
  });

  it("keeps the prompt preview integrated with the card surface", () => {
    const { container } = render(
      <PlaybookOperationCard
        operation={{ ...proposedOperation, state: "succeeded" }}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    const preview = container.querySelector(
      '[data-slot="playbook-prompt-preview"]',
    );
    expect(preview).toBeTruthy();
    expect(preview?.classList.contains("bg-surface")).toBe(false);
  });
});

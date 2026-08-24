import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AutomationProposalCard } from "./AutomationProposalCard";

vi.mock("../../hooks/use-i18n", () => ({
  useI18n: () => ({ t: (key: string) => key }),
}));

function renderCard(state: "pending" | "confirmed") {
  return render(
    <AutomationProposalCard
      name="每日跟踪"
      actionKind="chat"
      state={state}
      onConfirm={vi.fn()}
      onDismiss={vi.fn()}
    />,
  );
}

describe("AutomationProposalCard", () => {
  it("offers retry after confirmation fails", () => {
    render(
      <AutomationProposalCard
        name="每日市场跟踪"
        actionKind="chat"
        state="error"
        errorMessage="API 422"
        onConfirm={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );

    const retry = screen.getByRole("button", { name: "common.retry" });
    expect(retry.hasAttribute("disabled")).toBe(false);
  });

  it("opens the full Prompt from a compact icon and renders Markdown", async () => {
    const { container } = render(
      <AutomationProposalCard
        name="每日市场跟踪"
        actionKind="chat"
        promptTemplate={"# 市场概览\n\n- 跟踪美债利率\n- 检查半导体景气"}
        state="pending"
        onConfirm={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );

    const preview = container.querySelector(
      '[data-slot="automation-prompt-preview"]',
    );
    expect(preview).toBeTruthy();
    expect(preview?.querySelector(".overflow-y-auto")).toBeNull();

    const details = screen.getByRole("button", {
      name: "automation.viewPrompt",
    });
    expect(details.getAttribute("data-size")).toBe("icon-xs");
    expect(details.textContent).toBe("");

    await userEvent.click(details);

    expect(
      screen.getByRole("heading", { name: "每日市场跟踪" }),
    ).toBeTruthy();
    expect(screen.getByRole("heading", { name: "市场概览" })).toBeTruthy();
    expect(screen.getByRole("list")).toBeTruthy();
  });

  it("uses the standard action hierarchy", () => {
    renderCard("pending");

    expect(
      screen.getByRole("button", { name: "common.cancel" }).getAttribute(
        "data-variant",
      ),
    ).toBe("outline");
    expect(
      screen.getByRole("button", {
        name: "automation.actionCreate",
      }).getAttribute("data-variant"),
    ).toBe("default");
    expect(
      screen
        .getByRole("button", { name: "automation.actionCreate" })
        .parentElement?.classList.contains("border-t"),
    ).toBe(false);
  });

  it("uses the shared semantic success surface", () => {
    const { container } = renderCard("confirmed");
    const card = container.querySelector(
      '[data-slot="automation-proposal-card"]',
    );

    expect(card?.classList.contains("border-success/40")).toBe(true);
    expect(card?.classList.contains("bg-success/5")).toBe(true);
  });
});

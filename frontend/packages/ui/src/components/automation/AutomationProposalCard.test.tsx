import { render, screen } from "@testing-library/react";
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

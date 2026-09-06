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
    expect(
      screen.getByRole("dialog").classList.contains("sm:max-w-3xl"),
    ).toBe(true);
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
      screen
        .getByRole("button", {
          name: "playbook.operation.createAction",
        })
        .getAttribute("data-variant"),
    ).toBe("default");
    expect(
      screen
        .getByRole("button", {
          name: "playbook.operation.createAction",
        })
        .parentElement?.classList.contains("border-t"),
    ).toBe(false);

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

  it.each([
    ["create", "playbook.operation.createAction"],
    ["update", "playbook.operation.updateAction"],
    ["metadata", "playbook.operation.updateAction"],
    ["status", "playbook.operation.updateAction"],
    ["retire", "playbook.operation.retireAction"],
    ["delete", "playbook.operation.deleteAction"],
  ] as const)("labels the %s proposal with its concrete action", (change, label) => {
    render(
      <PlaybookOperationCard
        operation={{
          ...proposedOperation,
          preview: { ...proposedOperation.preview, change },
        }}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: label })).toBeTruthy();
  });

  it("uses retry only after a failed Playbook operation", () => {
    render(
      <PlaybookOperationCard
        operation={{ ...proposedOperation, state: "failed" }}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "common.retry" })).toBeTruthy();
  });

  it("asks for a comment before requesting changes and echoes the feedback", async () => {
    const onRequestChanges = vi.fn();
    const { rerender } = render(
      <PlaybookOperationCard
        operation={proposedOperation}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
        onRequestChanges={onRequestChanges}
      />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: "playbook.operation.requestChanges" }),
    );
    const send = screen.getByRole("button", {
      name: "playbook.operation.requestChangesSubmit",
    });
    expect(send.hasAttribute("disabled")).toBe(true);
    await userEvent.type(
      screen.getByRole("textbox", {
        name: "playbook.operation.requestChangesTitle",
      }),
      "  补充风险提示  ",
    );
    await userEvent.click(send);

    expect(onRequestChanges).toHaveBeenCalledWith("补充风险提示");
    expect(screen.queryByRole("dialog")).toBeNull();

    rerender(
      <PlaybookOperationCard
        operation={{
          ...proposedOperation,
          state: "awaiting_confirmation",
          latest_decision: {
            decision: "request_changes",
            comment: "补充风险提示",
          },
        }}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
        onRequestChanges={onRequestChanges}
      />,
    );
    expect(
      screen.getByText("playbook.operation.changesRequested: 补充风险提示"),
    ).toBeTruthy();
    // still decidable as proposed
    expect(
      screen.getByRole("button", { name: "playbook.operation.createAction" }),
    ).toBeTruthy();
  });

  it("hides the request-changes action without a handler", () => {
    render(
      <PlaybookOperationCard
        operation={proposedOperation}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(
      screen.queryByRole("button", {
        name: "playbook.operation.requestChanges",
      }),
    ).toBeNull();
  });

  it.each(["expired", "superseded"] as const)(
    "renders a %s proposal as dismissed with no actions",
    (state) => {
      const { container } = render(
        <PlaybookOperationCard
          operation={{ ...proposedOperation, state }}
          onConfirm={vi.fn()}
          onCancel={vi.fn()}
          onRequestChanges={vi.fn()}
        />,
      );
      expect(screen.getByText(`playbook.operation.${state}`)).toBeTruthy();
      expect(screen.queryAllByRole("button", { name: /Action$/ })).toEqual([]);
      const card = container.querySelector(
        '[data-slot="playbook-operation-card"]',
      );
      expect(card?.classList.contains("opacity-80")).toBe(true);
    },
  );

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

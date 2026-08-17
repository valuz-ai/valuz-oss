import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useRegistryStore } from "@valuz/core";
import { DropdownMenuItem } from "@valuz/ui";
import { AgentDetailCopyActions } from "./AgentDetailCopyActions";

vi.mock("@valuz/core", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@valuz/core")>()),
  useTranslation: () => ({ t: (key: string) => key }),
}));

const slotId = "test-agent-copy-menu-item";

function registerCopyMenuItem() {
  act(() => {
    useRegistryStore
      .getState()
      .registerSlot("resource.agent.copy.menu-items", {
        id: slotId,
        component: () => (
          <DropdownMenuItem>commercial.copyToOrganizations</DropdownMenuItem>
        ),
      });
  });
}

describe("AgentDetailCopyActions", () => {
  afterEach(() => {
    act(() => {
      useRegistryStore
        .getState()
        .unregisterSlot("resource.agent.copy.menu-items", slotId);
    });
  });

  it("keeps export and copy as separate buttons without contributed menu items", () => {
    const onExport = vi.fn();
    const onCopy = vi.fn();

    render(
      <AgentDetailCopyActions
        resource={{ slug: "course-builder" }}
        isSystem={false}
        onExport={onExport}
        onCopy={onCopy}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "agent.pack.export" }));
    fireEvent.click(screen.getByRole("button", { name: "agent.copyAgent" }));

    expect(onExport).toHaveBeenCalledOnce();
    expect(onCopy).toHaveBeenCalledOnce();
  });

  it("moves export into the copy menu when menu items are contributed", async () => {
    const onExport = vi.fn();
    const onCopy = vi.fn();
    registerCopyMenuItem();

    render(
      <AgentDetailCopyActions
        resource={{ slug: "course-builder" }}
        isSystem={false}
        onExport={onExport}
        onCopy={onCopy}
      />,
    );

    expect(
      screen.queryByRole("button", { name: "agent.pack.export" }),
    ).toBeNull();

    const trigger = screen.getByRole("button", { name: "agent.copyAgent" });
    await userEvent.click(trigger);

    fireEvent.click(
      screen.getByRole("menuitem", { name: "agent.pack.export" }),
    );
    expect(onExport).toHaveBeenCalledOnce();

    await userEvent.click(trigger);
    fireEvent.click(
      screen.getByRole("menuitem", { name: "agent.copyAgent" }),
    );
    expect(onCopy).toHaveBeenCalledOnce();
  });
});

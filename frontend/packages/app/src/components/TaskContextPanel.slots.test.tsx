/**
 * Extension points on the task context panel: ``task.panel.tabs`` and
 * ``task.panel.header.actions``.
 */

import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { initI18n } from "@valuz/shared/i18n";
import { useRegistryStore } from "@valuz/core";
import { TaskContextPanel } from "./TaskContextPanel";

// The file-tree prop is what switches the panel into its tabbed shell.
const renderPanel = () =>
  render(
    <TaskContextPanel
      projectId="p1"
      runs={[]}
      members={[]}
      taskStatus="active"
      fileTree={[]}
    />,
  );

describe("TaskContextPanel slots", () => {
  beforeEach(() => {
    initI18n({ locale: "en-US", fallbackLocale: "en-US" });
    useRegistryStore.setState({ slots: {} });
  });
  afterEach(() => {
    useRegistryStore.setState({ slots: {} });
  });

  it("adds no tab trigger and no header wrapper while nothing is registered", () => {
    const { container } = renderPanel();
    expect(screen.getAllByRole("tab")).toHaveLength(2);
    expect(container.querySelector("header .ml-auto")).toBeNull();
  });

  it("adds a trigger per registered tab and shows its content, with the documented context, when selected", async () => {
    const user = userEvent.setup();
    act(() => {
      useRegistryStore.getState().registerSlot("task.panel.tabs", {
        id: "cost-tab",
        key: "cost",
        label: "Cost",
        component: (props: Record<string, unknown>) => (
          <p data-testid="cost-body">
            {String(props.projectId)}:{String(props.taskStatus)}:
            {String((props.runs as unknown[]).length)}:
            {String((props.members as unknown[]).length)}
          </p>
        ),
      });
    });
    renderPanel();
    expect(screen.getAllByRole("tab")).toHaveLength(3);
    expect(screen.queryByTestId("cost-body")).toBeNull();

    await user.click(screen.getByRole("tab", { name: "Cost" }));
    expect(screen.getByTestId("cost-body").textContent).toBe("p1:active:0:0");
  });

  it("renders header actions at the right end of the tab header", () => {
    act(() => {
      useRegistryStore.getState().registerSlot("task.panel.header.actions", {
        id: "hdr",
        component: () => <button type="button">task-action</button>,
      });
    });
    const { container } = renderPanel();
    const wrapper = container.querySelector("header .ml-auto");
    expect(wrapper).not.toBeNull();
    expect(wrapper?.textContent).toBe("task-action");
  });
});

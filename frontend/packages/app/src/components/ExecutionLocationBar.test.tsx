import { useRegistryStore } from "@valuz/core";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ReactNode } from "react";

import { ExecutionLocationBar } from "./ExecutionLocationBar";

const noop = () => undefined;

function renderBar(over: Record<string, unknown> = {}) {
  /* eslint-disable @typescript-eslint/no-explicit-any */
  const props: any = {
    targetId: null,
    onTargetChange: noop,
    projects: [{ id: "p1", name: "Proj One" }],
    selectedProjectId: "p1",
    onProjectChange: noop,
    ...over,
  };
  /* eslint-enable @typescript-eslint/no-explicit-any */
  return render(<ExecutionLocationBar {...props} />);
}

const register = (
  component: (props: Record<string, unknown>) => ReactNode,
) =>
  act(() => {
    useRegistryStore.getState().registerSlot("conversation.composer.bar", {
      id: "t-bar",
      component: component as never,
    });
  });

describe("ExecutionLocationBar conversation.composer.bar", () => {
  beforeEach(() => {
    useRegistryStore.setState({ slots: {} });
  });
  afterEach(() => {
    cleanup();
    act(() => useRegistryStore.setState({ slots: {} }));
  });

  it("is exactly the stock strip while the slot is empty", () => {
    const { container } = renderBar();
    const strip = container.querySelector<HTMLElement>(
      "[data-slot='execution-location-bar']",
    )!;
    // Only the project chip on a single-target build; nothing else.
    expect(strip.children).toHaveLength(1);
    expect(strip.children[0]!.textContent).toContain("Proj One");
  });

  it("renders at the end of the strip, defaulting surface to conversation", () => {
    let ctx: Record<string, unknown> = {};
    register((props) => {
      ctx = props;
      return <i data-testid="ext-bar" />;
    });
    const { container } = renderBar({ locked: true });
    const strip = container.querySelector<HTMLElement>(
      "[data-slot='execution-location-bar']",
    )!;
    expect(strip.lastElementChild).toBe(screen.getByTestId("ext-bar"));
    expect(ctx).toEqual({
      targetId: null,
      locked: true,
      selectedProjectId: "p1",
      surface: "conversation",
    });
  });

  it("passes the project-home surface through", () => {
    let ctx: Record<string, unknown> = {};
    register((props) => {
      ctx = props;
      return null;
    });
    renderBar({ surface: "project-home" });
    expect(ctx.surface).toBe("project-home");
    // ``locked`` defaults to false when the host does not pass it.
    expect(ctx.locked).toBe(false);
  });
});

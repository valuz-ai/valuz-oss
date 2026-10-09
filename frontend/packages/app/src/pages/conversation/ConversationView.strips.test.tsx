import { useRegistryStore } from "@valuz/core";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ReactNode } from "react";

import { ConversationStrips } from "./ConversationView";

const register = (
  name: string,
  component: (props: Record<string, unknown>) => ReactNode,
) =>
  act(() => {
    useRegistryStore
      .getState()
      .registerSlot(name, { id: `t-${name}`, component: component as never });
  });

describe("ConversationStrips (conversation.strips)", () => {
  beforeEach(() => {
    useRegistryStore.setState({ slots: {} });
  });
  afterEach(() => {
    cleanup();
    act(() => useRegistryStore.setState({ slots: {} }));
  });

  const strips = (variant: "page" | "panel" = "page") => (
    <ConversationStrips
      sessionId="s1"
      projectId="p1"
      session={null}
      busy={true}
      variant={variant}
    />
  );

  it("renders no element at all while the slot is empty", () => {
    const { container } = render(strips());
    expect(container.firstChild).toBeNull();
  });

  it("renders the contribution in the 760px column, with the documented context", () => {
    let ctx: Record<string, unknown> = {};
    register("conversation.strips", (props) => {
      ctx = props;
      return <div data-testid="ext-strip" />;
    });
    render(strips("panel"));

    const column = screen.getByTestId("ext-strip").parentElement!;
    expect(column.className).toContain("max-w-[760px]");
    expect(column.className).toContain("px-4");
    expect(ctx).toEqual({
      sessionId: "s1",
      projectId: "p1",
      session: null,
      busy: true,
      variant: "panel",
    });
  });

  it("appears once a plugin registers after the first render", () => {
    const { container } = render(strips());
    expect(container.firstChild).toBeNull();
    register("conversation.strips", () => <div data-testid="ext-strip" />);
    expect(screen.getByTestId("ext-strip")).toBeTruthy();
  });
});

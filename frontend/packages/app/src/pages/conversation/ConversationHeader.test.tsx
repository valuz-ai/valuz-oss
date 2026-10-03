import { useRegistryStore } from "@valuz/core";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ConversationHeader } from "./ConversationHeader";

const noop = () => undefined;

function renderHeader(over: Record<string, unknown> = {}) {
  /* eslint-disable @typescript-eslint/no-explicit-any */
  const props: any = {
    fromTaskId: null,
    isSkillCreatorMode: false,
    headerTitle: "My chat",
    titleRenaming: false,
    titleRenameValue: "",
    setTitleRenameValue: noop,
    selectedSession: { id: "s1", name: "My chat", status: "idle" },
    selectedSessionId: "s1",
    refreshActiveSession: noop,
    setTitleRenaming: noop,
    titleRenameWidth: null,
    setTitleRenameWidth: noop,
    titleTriggerRef: { current: null },
    setTitleDeleting: noop,
    draftSendInFlight: false,
    effectiveTurns: [],
    scrollToTop: noop,
    headerAgentSlug: "valurion",
    agentNameBySlug: new Map([["valurion", "Valurion"]]),
    activeProject: { id: "p1", name: "Proj One" },
    onFork: noop,
    forkInFlight: false,
    ...over,
  };
  /* eslint-enable @typescript-eslint/no-explicit-any */
  return render(
    <MemoryRouter>
      <ConversationHeader {...props} />
    </MemoryRouter>,
  );
}

const register = (
  name: string,
  id: string,
  component: (props: Record<string, unknown>) => React.ReactNode,
) =>
  useRegistryStore
    .getState()
    .registerSlot(name, { id, component: component as never });

/** The identity cluster: the flex row holding the title, pill and badges. */
const cluster = (container: HTMLElement) =>
  container.querySelector<HTMLElement>("header .min-w-0.items-center.gap-2")!;

describe("ConversationHeader slots", () => {
  beforeEach(() => {
    useRegistryStore.setState({ slots: {} });
  });
  afterEach(() => {
    cleanup();
    act(() => useRegistryStore.setState({ slots: {} }));
  });

  describe("conversation.header.leading", () => {
    it("adds no child to the identity cluster while empty", () => {
      const { container } = renderHeader();
      const children = Array.from(cluster(container).children);
      // Title trigger first, project badge last: nothing on either side.
      expect(children[0]!.textContent).toContain("My chat");
      expect(children[children.length - 1]!.textContent).toBe("Proj One");
      expect(container.querySelector("[data-testid^='ext-']")).toBeNull();
    });

    it("renders first in the cluster with the documented context", () => {
      const seen: Array<Record<string, unknown>> = [];
      register("conversation.header.leading", "lead", (props) => {
        seen.push(props);
        return <i data-testid="ext-leading" />;
      });
      const { container } = renderHeader({ fromTaskId: "t9" });

      expect(cluster(container).firstElementChild).toBe(
        screen.getByTestId("ext-leading"),
      );
      expect(seen[0]).toMatchObject({
        sessionId: "s1",
        session: { id: "s1" },
        project: { id: "p1" },
        fromTaskId: "t9",
      });
    });
  });

  describe("conversation.header.badges", () => {
    it("renders after the project badge, last in the cluster, with agentSlug", () => {
      const seen: Array<Record<string, unknown>> = [];
      register("conversation.header.badges", "badge", (props) => {
        seen.push(props);
        return <i data-testid="ext-badge" />;
      });
      const { container } = renderHeader();

      const badge = screen.getByTestId("ext-badge");
      expect(cluster(container).lastElementChild).toBe(badge);
      expect(badge.previousElementSibling?.textContent).toBe("Proj One");
      expect(seen[0]).toMatchObject({
        sessionId: "s1",
        session: { id: "s1" },
        project: { id: "p1" },
        agentSlug: "valurion",
      });
    });
  });

  describe("conversation.title.menu-items", () => {
    const openMenu = async () => {
      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: /My chat/ }));
      return screen.findByRole("menu");
    };

    it("leaves the menu as Rename / separator / Delete while empty", async () => {
      renderHeader();
      const menu = await openMenu();
      expect(
        Array.from(menu.children).map((el) => el.getAttribute("role")),
      ).toEqual(["menuitem", "separator", "menuitem"]);
    });

    it("renders after Fork and before the separator, with the context", async () => {
      const seen: Array<Record<string, unknown>> = [];
      register("conversation.title.menu-items", "item", (props) => {
        seen.push(props);
        return <div role="menuitem" data-testid="ext-menu-item" />;
      });
      const turns = [{ id: "turn-1" }];
      renderHeader({
        selectedSession: {
          id: "s1",
          name: "My chat",
          status: "idle",
          runtime_provider: "codex",
        },
        effectiveTurns: turns,
      });
      const menu = await openMenu();

      const item = within(menu).getByTestId("ext-menu-item");
      // Rename, Fork, <contributed>, separator, Delete.
      expect(item.previousElementSibling?.textContent).toContain("Fork");
      expect(item.nextElementSibling?.getAttribute("role")).toBe("separator");
      expect(seen[0]).toMatchObject({
        sessionId: "s1",
        session: { id: "s1" },
        turns,
      });
    });

    it("has no menu to contribute to while the title is being renamed", () => {
      register("conversation.title.menu-items", "item", () => (
        <div role="menuitem" data-testid="ext-menu-item" />
      ));
      renderHeader({ titleRenaming: true });
      expect(screen.queryByTestId("ext-menu-item")).toBeNull();
    });
  });
});

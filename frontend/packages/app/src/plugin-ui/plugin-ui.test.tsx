/** @vitest-environment jsdom */

import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  _resetUiBusForTests,
  SingleSlot,
  SlotRenderer,
  uiApi,
  uiBus,
  useRegistryStore,
  type UiItem,
} from "@valuz/core";

import { startPluginUi } from "./host";
import { PluginSurface } from "./PluginSurface";
import { surfaceRequest, wireContext } from "./surface-request";
import { UiTree } from "./UiTree";

const { toastFn } = vi.hoisted(() => {
  const fn = vi.fn() as ReturnType<typeof vi.fn> & Record<string, unknown>;
  fn.success = vi.fn();
  fn.warning = vi.fn();
  fn.error = vi.fn();
  return { toastFn: fn };
});
vi.mock("sonner", () => ({ toast: toastFn }));

const slots = () => useRegistryStore.getState().slots;

beforeEach(() => {
  _resetUiBusForTests();
  vi.restoreAllMocks();
  toastFn.mockReset();
});

afterEach(() => {
  _resetUiBusForTests();
});

describe("UiTree", () => {
  it("draws elements with design-system components and reports actions", () => {
    const onAction = vi.fn();
    render(
      <UiTree
        tree={{
          type: "Box",
          props: { direction: "row", gap: 2 },
          children: [
            "plain",
            { type: "Text", props: { text: "muted text", tone: "muted" } },
            { type: "Badge", props: { text: "beta", tone: "warning" } },
            { type: "Button", props: { label: "Run", action: "run" } },
            { type: "Link", props: { href: "https://valuz.cn", text: "site" } },
            { type: "Input", props: { action: "ask", placeholder: "Ask" } },
            {
              type: "Select",
              props: {
                action: "pick",
                value: "a",
                options: [
                  { value: "a", label: "A" },
                  { value: "b", label: "B" },
                ],
              },
            },
            { type: "Unknown" },
          ],
        }}
        onAction={onAction}
      />,
    );
    expect(screen.getByText("plain")).toBeTruthy();
    expect(screen.getByText("muted text").className).toContain(
      "text-ink-muted",
    );
    expect(screen.getByText("beta").getAttribute("data-variant")).toBe(
      "warning",
    );
    expect(screen.getByText("site").getAttribute("rel")).toBe(
      "noreferrer noopener",
    );

    fireEvent.click(screen.getByText("Run"));
    const input = screen.getByPlaceholderText("Ask");
    fireEvent.change(input, { target: { value: "why?" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.change(screen.getByDisplayValue("A"), { target: { value: "b" } });
    expect(onAction.mock.calls).toEqual([
      ["run"],
      ["ask", "why?"],
      ["pick", "b"],
    ]);
  });

  it("draws Default as the host's own content, with overrides", () => {
    const renderDefault = vi.fn((overrides?: Record<string, unknown>) => (
      <span>default {String(overrides?.tone ?? "")}</span>
    ));
    render(
      <UiTree
        tree={{
          type: "Box",
          children: [
            { type: "Default", props: { overrides: { tone: "x" } } },
            { type: "Text", props: { text: "after" } },
          ],
        }}
        onAction={vi.fn()}
        renderDefault={renderDefault}
      />,
    );
    expect(screen.getByText("default x")).toBeTruthy();
    expect(screen.getByText("after")).toBeTruthy();
  });
});

describe("PluginSurface", () => {
  const item = (label: string, generation: number, key?: string): UiItem => ({
    owner: "counter",
    tree: { type: "Button", props: { label, action: "inc" } },
    generation,
    ...(key ? { key } : {}),
  });

  it("sends the slot context as primitives and names the instance by its ids", () => {
    const request = surfaceRequest("conversation.tool-call", undefined, {
      sessionId: "s1",
      toolUseId: "t1",
      status: "running",
      tool: { id: "t1" },
      renderDefault: () => null,
      text: "x".repeat(5000),
    });
    expect(request.instance).toBe("s1:t1");
    expect(request.session_id).toBe("s1");
    expect(request.context).not.toHaveProperty("tool");
    expect(request.context).not.toHaveProperty("renderDefault");
    expect((request.context!.text as string).length).toBe(4000);
    expect(wireContext({ a: 1, b: true, c: null, d: undefined })).toEqual({
      a: 1,
      b: true,
      c: null,
    });
  });

  it("draws a list site, delivers clicks with the drawing's generation, and redraws", async () => {
    const renderSpy = vi
      .spyOn(uiApi, "render")
      .mockResolvedValueOnce([item("clicked 0", 1)])
      .mockResolvedValueOnce([item("clicked 1 (redrawn)", 3)]);
    const actionSpy = vi
      .spyOn(uiApi, "action")
      .mockResolvedValue({ ok: true, items: [item("clicked 1", 2)] });

    render(
      <PluginSurface
        site="conversation.composer.dock"
        kind="list"
        context={{ sessionId: "s1", draft: "", setDraft: () => {} }}
      />,
    );
    fireEvent.click(await screen.findByText("clicked 0"));
    await screen.findByText("clicked 1");
    expect(actionSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        site: "conversation.composer.dock",
        instance: "s1",
        owner: "counter",
        action: "inc",
        generation: 1,
      }),
    );

    act(() => {
      uiBus.emit({
        kind: "invalidate",
        sessionId: "s1",
        payload: { site: "conversation.composer.dock" },
      });
    });
    await screen.findByText("clicked 1 (redrawn)");
    expect(renderSpy).toHaveBeenCalledTimes(2);
  });

  it("shows the slot's own content for a single site until and unless a plugin draws", async () => {
    let resolve!: (items: UiItem[]) => void;
    vi.spyOn(uiApi, "render").mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    render(
      <PluginSurface
        site="conversation.tool-call"
        kind="single"
        context={{ toolUseId: "t1" }}
        renderDefault={() => <span>the card</span>}
      />,
    );
    expect(screen.getByText("the card")).toBeTruthy();
    await act(async () => {
      resolve([
        {
          owner: "wrapper",
          generation: 1,
          tree: {
            type: "Box",
            children: [
              { type: "Default" },
              { type: "Badge", props: { text: "checked" } },
            ],
          },
        },
      ]);
    });
    expect(await screen.findByText("checked")).toBeTruthy();
    expect(screen.getByText("the card")).toBeTruthy();
  });

  it("draws only its own key on a keyed site", async () => {
    vi.spyOn(uiApi, "render").mockResolvedValue([
      item("council", 1, "council"),
      item("other", 2, "other"),
    ]);
    render(
      <PluginSurface
        site="context-panel.tabs"
        kind="keyed"
        slotKey="council"
        context={{}}
      />,
    );
    await screen.findByText("council");
    expect(screen.queryByText("other")).toBeNull();
  });
});

describe("startPluginUi", () => {
  it("fetches nothing and mounts nothing until the backend announces plugin UI", async () => {
    const sites = vi.spyOn(uiApi, "sites").mockResolvedValue([
      { site: "conversation.composer.dock", kind: "list" },
      { site: "conversation.tool-call", kind: "single" },
      {
        site: "context-panel.tabs",
        kind: "keyed",
        key: "council",
        label: "Council",
      },
    ]);
    const stop = startPluginUi();
    try {
      expect(sites).not.toHaveBeenCalled();
      expect(slots()["conversation.composer.dock"] ?? []).toEqual([]);

      act(() => {
        uiBus.emit({
          kind: "invalidate",
          sessionId: null,
          payload: { site: "*" },
        });
      });
      await waitFor(() =>
        expect(
          slots()["context-panel.tabs"]?.map((r) => [r.id, r.key, r.label]),
        ).toEqual([
          ["plugin-ui:context-panel.tabs:council", "council", "Council"],
        ]),
      );
      expect(slots()["conversation.composer.dock"]?.map((r) => r.id)).toEqual([
        "plugin-ui:conversation.composer.dock",
      ]);
      expect(slots()["conversation.tool-call"]?.map((r) => r.id)).toEqual([
        "plugin-ui:conversation.tool-call",
      ]);

      // A site that goes away is unmounted on the next announcement.
      sites.mockResolvedValue([
        { site: "conversation.tool-call", kind: "single" },
      ]);
      act(() => {
        uiBus.emit({ kind: "invalidate", sessionId: null, payload: {} });
      });
      await waitFor(() =>
        expect(slots()["conversation.composer.dock"] ?? []).toEqual([]),
      );
      expect(slots()["context-panel.tabs"] ?? []).toEqual([]);
    } finally {
      stop();
    }
    expect(slots()["conversation.tool-call"] ?? []).toEqual([]);
  });

  it("retries the sites read with back-off while the backend is not there", async () => {
    vi.useFakeTimers();
    try {
      const sites = vi
        .spyOn(uiApi, "sites")
        .mockRejectedValueOnce(new Error("offline"))
        .mockResolvedValue([]);
      const stop = startPluginUi();
      uiBus.emit({
        kind: "invalidate",
        sessionId: null,
        payload: { site: "*" },
      });
      await vi.advanceTimersByTimeAsync(0);
      expect(sites).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(2000);
      expect(sites).toHaveBeenCalledTimes(2);
      stop();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows toasts and mounts status / notice / log surfaces on their first push", async () => {
    const stop = startPluginUi();
    try {
      act(() => {
        uiBus.emit({
          kind: "toast",
          sessionId: null,
          payload: { text: "saved", tone: "success" },
        });
        uiBus.emit({
          kind: "toast",
          sessionId: null,
          payload: { text: "fyi" },
        });
      });
      expect(toastFn.success).toHaveBeenCalledWith("saved");
      expect(toastFn).toHaveBeenCalledWith("fyi");
      expect(slots()["conversation.composer.dock"] ?? []).toEqual([]);

      act(() => {
        uiBus.emit({
          kind: "status",
          sessionId: "s1",
          payload: { owner: "indexer", text: "indexing 3 files" },
        });
        uiBus.emit({
          kind: "notice",
          sessionId: "s1",
          payload: {
            owner: "lint",
            tool_use_id: "t1",
            text: "2 warnings",
            tone: "warning",
          },
        });
        uiBus.emit({
          kind: "log",
          sessionId: "s1",
          payload: { owner: "audit", text: "audit passed" },
          timestamp: 50,
        });
      });
      render(
        <>
          <SlotRenderer
            name="conversation.composer.dock"
            context={{ sessionId: "s1" }}
          />
          <SlotRenderer
            name="conversation.composer.dock"
            context={{ sessionId: "s2" }}
          />
          <SingleSlot
            name="conversation.tool-call"
            context={{ toolUseId: "t1" }}
          >
            <span>tool card</span>
          </SingleSlot>
          <SlotRenderer
            name="conversation.turn.tail"
            context={{
              sessionId: "s1",
              isLatest: true,
              turn: { id: "turn1", userTimestamp: 10 },
            }}
          />
        </>,
      );
      expect(screen.getAllByText("indexing 3 files")).toHaveLength(1);
      expect(screen.getByText("tool card")).toBeTruthy();
      expect(screen.getByText("2 warnings").className).toContain(
        "text-warning-text",
      );
      expect(screen.getByText("audit passed")).toBeTruthy();
    } finally {
      stop();
    }
    expect(slots()["conversation.composer.dock"] ?? []).toEqual([]);
    expect(slots()["conversation.tool-call"] ?? []).toEqual([]);
    expect(slots()["conversation.turn.tail"] ?? []).toEqual([]);
  });
});

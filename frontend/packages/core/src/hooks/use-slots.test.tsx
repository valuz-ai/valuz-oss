import { renderHook, act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useRegistryStore } from "../edition/registry-store";
import { useRegisteredSlotNames } from "./use-slots";

const Dummy = () => null;

describe("useRegisteredSlotNames", () => {
  beforeEach(() => {
    useRegistryStore.setState({ slots: {} });
  });

  it("is empty when no overlay has registered anything", () => {
    const { result } = renderHook(() => useRegisteredSlotNames());
    expect(result.current.size).toBe(0);
  });

  it("reports a name once something is registered for it", () => {
    const { result } = renderHook(() => useRegisteredSlotNames());
    act(() => {
      useRegistryStore
        .getState()
        .registerSlot("conversation.tool-card.site_deploy", {
          id: "commercial",
          component: Dummy,
        });
    });
    expect(result.current.has("conversation.tool-card.site_deploy")).toBe(true);
  });

  it("drops a name whose last registration was removed", () => {
    // ``unregisterSlot`` leaves the key behind with an EMPTY array rather than
    // deleting it. A host that reads bare key presence would then skip its own
    // fallback forever while rendering nothing — a blank card that no amount of
    // re-registering fixes. This is the assertion that keeps the filter honest.
    const { result } = renderHook(() => useRegisteredSlotNames());
    act(() => {
      useRegistryStore.getState().registerSlot("s", {
        id: "a",
        component: Dummy,
      });
    });
    expect(result.current.has("s")).toBe(true);

    act(() => {
      useRegistryStore.getState().unregisterSlot("s", "a");
    });
    expect(useRegistryStore.getState().slots.s).toEqual([]);
    expect(result.current.has("s")).toBe(false);
  });

  it("keeps a stable identity across renders that change nothing", () => {
    // Consumers put this in dependency arrays (``useToolCallCards`` does). A
    // fresh Set per render would invalidate their callbacks on every render.
    const { result, rerender } = renderHook(() => useRegisteredSlotNames());
    const first = result.current;
    rerender();
    expect(result.current).toBe(first);

    act(() => {
      useRegistryStore.getState().registerSlot("s", {
        id: "a",
        component: Dummy,
      });
    });
    const afterRegister = result.current;
    expect(afterRegister).not.toBe(first);
    rerender();
    expect(result.current).toBe(afterRegister);
  });
});

describe("slot rendering", () => {
  beforeEach(() => {
    useRegistryStore.setState({ slots: {} });
  });

  const Hello = ({ who }: Record<string, unknown>) => <span>hello {String(who)}</span>;
  const Boom = (): never => {
    throw new Error("contribution exploded");
  };

  it("isolates a crashing contribution so its siblings still render", async () => {
    const { render, screen } = await import("@testing-library/react");
    const { SlotRenderer } = await import("./use-slots");
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    const store = useRegistryStore.getState();
    store.registerSlot("test.list", { id: "boom", component: Boom });
    store.registerSlot("test.list", { id: "hello", component: Hello });

    render(<SlotRenderer name="test.list" context={{ who: "world" }} />);

    // Before the boundary, one throwing contribution unmounted the whole
    // surface it was rendered into.
    expect(screen.getByText("hello world")).toBeTruthy();
    expect(
      logged.mock.calls.some((call) => String(call[0]).includes("test.list")),
    ).toBe(true);
    logged.mockRestore();
  });

  it("SingleSlot renders the winning occupant, or its own default when empty", async () => {
    const { render, screen } = await import("@testing-library/react");
    const { SingleSlot } = await import("./use-slots");
    const view = render(
      <SingleSlot name="test.single" context={{ who: "plugin" }}>
        <span>default</span>
      </SingleSlot>,
    );
    expect(screen.getByText("default")).toBeTruthy();

    act(() => {
      useRegistryStore.getState().registerSlot("test.single", { id: "a", component: Hello });
      useRegistryStore
        .getState()
        .registerSlot("test.single", { id: "b", component: () => <span>winner</span>, priority: -1 });
    });
    view.rerender(
      <SingleSlot name="test.single" context={{ who: "plugin" }}>
        <span>default</span>
      </SingleSlot>,
    );
    expect(screen.getByText("winner")).toBeTruthy();
    expect(screen.queryByText("default")).toBeNull();
    expect(screen.queryByText("hello plugin")).toBeNull();
  });

  it("KeyedSlot dispatches by key and falls back for unclaimed keys", async () => {
    const { render, screen } = await import("@testing-library/react");
    const { KeyedSlot } = await import("./use-slots");
    useRegistryStore.getState().registerSlot("test.keyed", {
      id: "bash-view",
      key: "bash",
      component: () => <span>bash view</span>,
    });

    render(
      <>
        <KeyedSlot name="test.keyed" slotKey="bash" fallback={<span>generic</span>} />
        <KeyedSlot name="test.keyed" slotKey="read" fallback={<span>generic</span>} />
      </>,
    );
    expect(screen.getByText("bash view")).toBeTruthy();
    expect(screen.getAllByText("generic")).toHaveLength(1);
  });

  it("useHasSlot reports whether anything occupies a slot (or a key)", async () => {
    const { useHasSlot } = await import("./use-slots");
    const { result } = renderHook(() => ({
      any: useHasSlot("test.keyed"),
      bash: useHasSlot("test.keyed", "bash"),
    }));
    expect(result.current).toEqual({ any: false, bash: false });
    act(() => {
      useRegistryStore.getState().registerSlot("test.keyed", {
        id: "bash-view",
        key: "bash",
        component: Dummy,
      });
    });
    expect(result.current).toEqual({ any: true, bash: true });
  });
  it("SlotRenderer with slotKey renders every contribution under that key", async () => {
    const { render, screen } = await import("@testing-library/react");
    const { SlotRenderer } = await import("./use-slots");
    const store = useRegistryStore.getState();
    store.registerSlot("test.footer", {
      id: "a",
      key: "model",
      component: () => <span>model a</span>,
    });
    store.registerSlot("test.footer", {
      id: "b",
      key: "model",
      component: () => <span>model b</span>,
    });
    store.registerSlot("test.footer", {
      id: "c",
      key: "general",
      component: () => <span>general c</span>,
    });

    render(<SlotRenderer name="test.footer" slotKey="model" />);
    expect(screen.getByText("model a")).toBeTruthy();
    expect(screen.getByText("model b")).toBeTruthy();
    expect(screen.queryByText("general c")).toBeNull();
  });

  it("lets a host draw its own chrome around each contribution", async () => {
    const { render, screen } = await import("@testing-library/react");
    const { SlotContribution, useSlotRegistrations } = await import(
      "./use-slots"
    );
    useRegistryStore.getState().registerSlot("test.tabs", {
      id: "usage",
      key: "usage",
      label: "Usage",
      component: Hello,
    });
    function Tabs() {
      const tabs = useSlotRegistrations("test.tabs");
      return (
        <>
          {tabs.map((reg) => (
            <section key={reg.id} aria-label={reg.label}>
              <SlotContribution
                name="test.tabs"
                registration={reg}
                context={{ who: "tab" }}
              />
            </section>
          ))}
        </>
      );
    }
    render(<Tabs />);
    expect(screen.getByLabelText("Usage").textContent).toBe("hello tab");
  });
});

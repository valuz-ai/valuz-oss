import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { getThirdPartyApiBase, setThirdPartyApiBase } from "@valuz/core";
import { initI18n, t } from "@valuz/shared/i18n";

vi.mock("sonner", async (importOriginal) => {
  const actual = await importOriginal<typeof import("sonner")>();
  return {
    ...actual,
    toast: Object.assign(vi.fn(), {
      success: vi.fn(),
      error: vi.fn(),
      warning: vi.fn(),
    }),
  };
});

import { toast } from "sonner";
import { ThirdPartyBridge } from "./bridge";
import {
  createThirdPartyHostServices,
  setThirdPartyOrgIdProvider,
  thirdPartyBridge,
} from "./services";

beforeAll(() => initI18n({ locale: "en-US", fallbackLocale: "en-US" }));

const originalBase = getThirdPartyApiBase();
beforeEach(() => setThirdPartyApiBase("http://backend.test"));
afterEach(() => {
  cleanup();
  setThirdPartyApiBase(originalBase);
  setThirdPartyOrgIdProvider(null);
  vi.restoreAllMocks();
});

describe("request", () => {
  it("goes through the request layer against the third-party base, with the plugin header and a query", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));

    const result = await createThirdPartyHostServices().request({
      method: "POST",
      path: "/v1/docs/search",
      query: { a: 1, skipped: undefined, empty: null },
      json: { query: "x" },
      headers: { "X-Valuz-Plugin-Id": "acme.dashboard" },
    });

    expect(result).toEqual({ ok: true });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("http://backend.test/v1/docs/search?a=1");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ query: "x" }));
    expect(new Headers(init?.headers).get("X-Valuz-Plugin-Id")).toBe("acme.dashboard");
  });

  it("rejects with the request layer's error (status and body kept)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: { code: "plugin_permission_denied", message: "no" } }), { status: 403 }),
    );
    await expect(
      createThirdPartyHostServices().request({ method: "GET", path: "/v1/projects" }),
    ).rejects.toMatchObject({ status: 403 });
  });
});

describe("host actions", () => {
  it("maps toast variants onto sonner", () => {
    const services = createThirdPartyHostServices();
    services.toast({ title: "a", variant: "success", description: "d" });
    services.toast({ title: "b", variant: "error" });
    services.toast({ title: "c", variant: "warning" });
    services.toast({ title: "d" });
    expect(toast.success).toHaveBeenCalledWith("a", { description: "d" });
    expect(toast.error).toHaveBeenCalledWith("b", undefined);
    expect(toast.warning).toHaveBeenCalledWith("c", undefined);
    expect(toast).toHaveBeenCalledWith("d", undefined);
  });

  it("opens external links in a new window outside the desktop shell", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    await createThirdPartyHostServices().openExternal("https://example.com");
    expect(open).toHaveBeenCalledWith("https://example.com", "_blank", "noopener,noreferrer");
  });

  it("uses the desktop shell's open-external IPC when there is one", async () => {
    const invoke = vi.fn().mockResolvedValue(true);
    (window as unknown as { valuzDesktop?: unknown }).valuzDesktop = { invoke };
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    try {
      await createThirdPartyHostServices().openExternal("https://example.com");
      expect(invoke).toHaveBeenCalledWith("open_external_url", { url: "https://example.com" });
      expect(open).not.toHaveBeenCalled();
    } finally {
      delete (window as unknown as { valuzDesktop?: unknown }).valuzDesktop;
    }
  });

  it("copies text to the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await createThirdPartyHostServices().copyText("hello");
    expect(writeText).toHaveBeenCalledWith("hello");
  });

  it("registers the plugin's locales under ext.<id>", () => {
    createThirdPartyHostServices().registerLocales!("acme.dashboard", {
      "en-US": { title: "Positions", nested: { key: "Nested" } },
    });
    expect(t("ext.acme.dashboard.title" as Parameters<typeof t>[0])).toBe("Positions");
    expect(t("ext.acme.dashboard.nested.key" as Parameters<typeof t>[0])).toBe("Nested");
  });

  it("tells plugins the host context", () => {
    setThirdPartyOrgIdProvider(() => "org-1");
    document.documentElement.classList.add("dark");
    try {
      expect(createThirdPartyHostServices().getHostContext()).toMatchObject({
        locale: "en-US",
        theme: "dark",
        deployment: "local",
        orgId: "org-1",
      });
    } finally {
      document.documentElement.classList.remove("dark");
    }
  });

  it("notifies host-context subscribers when the theme class changes", async () => {
    const listener = vi.fn();
    const off = createThirdPartyHostServices().subscribeHostContext!(listener);
    document.documentElement.classList.add("dark");
    await vi.waitFor(() => expect(listener).toHaveBeenCalled());
    document.documentElement.classList.remove("dark");
    off();
  });
});

function Where() {
  const location = useLocation();
  return (
    <output data-testid="where">
      {location.pathname + location.search}|{JSON.stringify(location.state)}
    </output>
  );
}

describe("ThirdPartyBridge", () => {
  const mount = () =>
    render(
      <MemoryRouter initialEntries={["/start"]}>
        <ThirdPartyBridge />
        <Where />
      </MemoryRouter>,
    );

  it("gives the services the router's navigate, and takes it back on unmount", () => {
    const view = mount();
    expect(thirdPartyBridge.navigate).toBeTypeOf("function");

    act(() => createThirdPartyHostServices().navigate("/x/acme.dashboard"));
    expect(screen.getByTestId("where").textContent).toBe("/x/acme.dashboard|null");

    view.unmount();
    expect(thirdPartyBridge.navigate).toBeNull();
    expect(thirdPartyBridge.confirm).toBeNull();
  });

  it("opens a new conversation with the draft in the navigation state", () => {
    mount();
    act(() => {
      void createThirdPartyHostServices().draftConversation({
        projectId: "p 1",
        agent: "lead",
        text: "explain this",
      });
    });
    expect(screen.getByTestId("where").textContent).toBe(
      '/conversation/new?project=p+1&agent=lead|{"draft":"explain this"}',
    );
  });

  it("asks with a host dialog and resolves the answer", async () => {
    mount();
    const services = createThirdPartyHostServices();

    let answer: Promise<boolean> | null = null;
    act(() => {
      answer = services.confirm({ title: "Run set_limit?", body: "{\"n\":1}" });
    });
    expect(await screen.findByText("Run set_limit?")).toBeTruthy();
    expect(screen.getByText('{"n":1}')).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await answer).toBe(true);

    act(() => {
      answer = services.confirm({ title: "Again?", confirmLabel: "Do it" });
    });
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(await answer).toBe(false);
    expect(screen.queryByText("Again?")).toBeNull();
  });

  it("answers a still-open dialog 'no' when another one comes", async () => {
    mount();
    const services = createThirdPartyHostServices();
    let first: Promise<boolean> | null = null;
    act(() => {
      first = services.confirm({ title: "First" });
    });
    await screen.findByText("First");
    act(() => {
      void services.confirm({ title: "Second" });
    });
    expect(await first).toBe(false);
    expect(await screen.findByText("Second")).toBeTruthy();
  });
});

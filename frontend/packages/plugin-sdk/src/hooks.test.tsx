import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { definePlugin } from "./define";
import { host } from "./host-api";
import {
  useHostContext,
  usePluginConfig,
  useTranslation,
  useValuz,
} from "./scope";
import { createTestHost, mockValuz } from "./testing";
import type { PluginDefinition } from "./types";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const LOCALES = {
  "en-US": { hello: "Hello {{name}}", only_en: "English only", nested: { key: "Nested" } },
  "zh-CN": { hello: "你好 {{name}}", nested: { key: "嵌套" } },
};

const roots: Root[] = [];
afterEach(() => {
  for (const root of roots.splice(0)) act(() => root.unmount());
});

function mount(node: React.ReactNode): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);
  act(() => root.render(node));
  return container;
}

async function loadPanel(
  Panel: () => React.ReactNode,
  options: Parameters<typeof createTestHost>[0] = {},
) {
  const testHost = createTestHost({ locales: LOCALES, ...options });
  const plugin: PluginDefinition = definePlugin({
    id: "acme.dashboard",
    apply(ctx) {
      ctx.registry.slot("shell.topbar.actions", { id: "panel", component: Panel });
    },
  });
  await testHost.load(plugin);
  const [Wrapped] = testHost.getSlotComponents("shell.topbar.actions");
  return { testHost, Wrapped: Wrapped! };
}

describe("useTranslation", () => {
  it("translates with the plugin's locales, interpolates {{var}} and follows the host locale", async () => {
    const Panel = () => {
      const { t, locale } = useTranslation();
      return (
        <p>
          {locale}|{t("hello", { name: "Ann" })}|{t("nested.key")}
        </p>
      );
    };
    const { testHost, Wrapped } = await loadPanel(Panel, { locale: "zh-CN" });
    const container = mount(<Wrapped />);
    expect(container.textContent).toBe("zh-CN|你好 Ann|嵌套");

    act(() => testHost.setLocale("en-US"));
    expect(container.textContent).toBe("en-US|Hello Ann|Nested");
  });

  it("falls back to en-US, then to the key; keeps unknown placeholders", async () => {
    const Panel = () => {
      const { t } = useTranslation();
      return (
        <p>
          {t("only_en")}|{t("missing.key")}|{t("hello")}
        </p>
      );
    };
    const { Wrapped } = await loadPanel(Panel, { locale: "zh-CN" });
    expect(mount(<Wrapped />).textContent).toBe("English only|missing.key|你好 {{name}}");
  });

  it("uses another variant of the same language before en-US", async () => {
    const Panel = () => <p>{useTranslation().t("hello", { name: "A" })}</p>;
    const { Wrapped } = await loadPanel(Panel, {
      locale: "zh-TW",
    });
    expect(mount(<Wrapped />).textContent).toBe("你好 A");
  });
});

describe("usePluginConfig / useHostContext / useValuz", () => {
  it("re-renders when the configuration changes", async () => {
    const Panel = () => <p>{String(usePluginConfig<{ region: string }>().region)}</p>;
    const { testHost, Wrapped } = await loadPanel(Panel, { config: { region: "cn" } });
    const container = mount(<Wrapped />);
    expect(container.textContent).toBe("cn");

    act(() => testHost.setConfig({ region: "hk" }));
    expect(container.textContent).toBe("hk");
  });

  it("gives the host context and follows changes", async () => {
    const Panel = () => {
      const ctx = useHostContext();
      return <p>{`${ctx.locale}/${ctx.theme}/${ctx.edition}/${ctx.deployment}/${ctx.orgId}`}</p>;
    };
    const { testHost, Wrapped } = await loadPanel(Panel, {
      hostContext: { orgId: "org1", edition: "finance" },
    });
    const container = mount(<Wrapped />);
    expect(container.textContent).toBe("en-US/light/finance/local/org1");

    act(() => testHost.setHostContext({ theme: "dark" }));
    expect(container.textContent).toBe("en-US/dark/finance/local/org1");
  });

  it("hands the plugin's client to components", async () => {
    const valuz = mockValuz({ projects: [{ id: "p1", name: "One", kind: null, rootPath: null, icon: null }] });
    let client: unknown;
    const Panel = () => {
      client = useValuz();
      return null;
    };
    const { Wrapped } = await loadPanel(Panel, { valuz });
    mount(<Wrapped />);
    expect(client).toBe(valuz);
  });

  it("throws outside a plugin component", () => {
    const Bare = () => <p>{useTranslation().locale}</p>;
    expect(() => mount(<Bare />)).toThrow(/must be called from a component the plugin registered/);
  });
});

describe("contribution wrapper", () => {
  it("renders inside the plugin's scope element and adapts the host props", async () => {
    const seen: unknown[] = [];
    const testHost = createTestHost();
    await testHost.load(
      definePlugin({
        id: "acme.dashboard",
        apply(ctx) {
          ctx.registry.slot("conversation.title.actions", {
            id: "count",
            component: (props) => {
              seen.push(props);
              return <b>{props.turns.length}</b>;
            },
          });
        },
      }),
    );
    const [Wrapped] = testHost.getSlotComponents("conversation.title.actions");
    const container = mount(
      <Wrapped
        sessionId="s1"
        session={{ id: "s1", name: "Title", project_id: "p1", agent_slug: "lead", created_at: 5, secret: 1 }}
        scrollToTop={() => {}}
        turns={[
          { id: "t1", userText: "hi", blocks: [{ kind: "assistant", text: "yo" }], failedMessage: null, userTimestamp: 3 },
        ]}
      />,
    );
    expect(container.innerHTML).toBe(
      '<div data-valuz-ext="acme.dashboard" style="display: contents;"><b>1</b></div>',
    );
    expect(seen[0]).toMatchObject({
      sessionId: "s1",
      session: { id: "s1", title: "Title", projectId: "p1", agentSlug: "lead", createdAt: 5 },
      turns: [{ id: "t1", userText: "hi", assistantText: "yo", createdAt: 3, failed: false }],
    });
    expect((seen[0] as { session: object }).session).not.toHaveProperty("secret");
  });

  it("keeps a crashing component from breaking its host, and logs it", async () => {
    const { testHost, Wrapped } = await loadPanel(() => {
      throw new Error("kaput");
    });
    const originalError = console.error;
    console.error = () => {};
    try {
      const container = mount(<Wrapped />);
      expect(container.textContent).toBe("");
    } finally {
      console.error = originalError;
    }
    expect(testHost.calls.toast).toEqual([]);
  });
});

describe("host", () => {
  it("calls the host services", async () => {
    const { testHost } = await loadPanel(() => null);
    host.navigate("/x/acme.dashboard");
    host.openSession("s 1");
    host.openProject("p1");
    host.toast({ title: "Saved" });
    expect(await host.confirm({ title: "Sure?" })).toBe(true);
    await host.openExternal("https://example.com");
    await host.copyText("copied");
    await host.draftConversation({ projectId: "p1", text: "hello" });

    expect(testHost.calls).toEqual({
      navigate: [
        { path: "/x/acme.dashboard", options: undefined },
        { path: "/conversation/s%201", options: undefined },
        { path: "/projects/p1", options: undefined },
      ],
      toast: [{ title: "Saved" }],
      confirm: [{ title: "Sure?" }],
      openExternal: ["https://example.com"],
      copyText: ["copied"],
      drafts: [{ projectId: "p1", text: "hello" }],
    });
  });

  it("refuses to open a non-http URL", async () => {
    await loadPanel(() => null);
    await expect(host.openExternal("file:///etc/passwd")).rejects.toThrow(/http/);
  });

  it("a declined confirm resolves false", async () => {
    const { testHost } = await loadPanel(() => null, { confirm: false });
    expect(await host.confirm({ title: "?" })).toBe(false);
    expect(testHost.calls.confirm).toHaveLength(1);
  });
});

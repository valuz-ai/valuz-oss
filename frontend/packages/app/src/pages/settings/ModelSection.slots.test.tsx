import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { initI18n } from "@valuz/shared/i18n";
import { useRegistryStore } from "@valuz/core";

// Two visible channels. (System-source channels are filtered out of this list by
// ``loadProvidersList``, so the rows the slot sees are never ``isSystem``.)
const { channels } = vi.hoisted(() => ({
  channels: [
    {
      id: "ch-user",
      name: "My Channel",
      source: "user",
      provider_kind: "openai",
      credential_source: "keychain",
      auth_type: "api_key",
      enabled: true,
      is_default: false,
      deletable: true,
      test_status: "success",
      models: [],
    },
    {
      id: "ch-oauth",
      name: "Subscription Channel",
      source: "user",
      provider_kind: "claude-subscription",
      credential_source: "none",
      auth_type: "oauth",
      enabled: true,
      is_default: false,
      deletable: false,
      test_status: "success",
      models: [],
    },
  ],
}));

vi.mock("@valuz/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@valuz/core")>();
  return {
    ...actual,
    providersApi: {
      ...actual.providersApi,
      list: vi.fn().mockResolvedValue({ providers: channels }),
      listProviders: vi.fn().mockResolvedValue({ providers: [] }),
    },
    runtimesApi: {
      ...actual.runtimesApi,
      list: vi.fn().mockResolvedValue({ runtimes: [] }),
    },
    settingsApi: {
      ...actual.settingsApi,
      getModelDefaults: vi.fn().mockResolvedValue({
        default_runtime: "claude_agent",
        default_provider_id: null,
        default_model: null,
        default_effort: "medium",
      }),
      getModelOptions: vi.fn().mockResolvedValue({ groups: [] }),
    },
  };
});

import { ModelSection } from "./ModelSection";

type Registration = Parameters<
  ReturnType<typeof useRegistryStore.getState>["registerSlot"]
>[1];
const register = (name: string, registration: Registration) =>
  useRegistryStore.getState().registerSlot(name, registration);

beforeAll(() => initI18n({ locale: "zh-CN", fallbackLocale: "zh-CN" }));
beforeEach(() => {
  useRegistryStore.setState({ slots: {} });
});

describe("ModelSection slots", () => {
  it("keeps the channel header row at two children while the slot is empty", async () => {
    render(<ModelSection />);
    const add = await screen.findByRole("button", { name: /添加/ });
    // Add sits straight in the ``justify-between`` row next to the title block:
    // no wrapper appears around it.
    expect(add.parentElement?.className).toContain("justify-between");
    expect(add.parentElement?.children).toHaveLength(2);
  });

  it("settings.model.channels.actions renders before Add and receives openAdd", async () => {
    let seen: { openAdd?: unknown } = {};
    register("settings.model.channels.actions", {
      id: "test-channel-action",
      component: (props: { openAdd?: unknown }) => {
        seen = props;
        return <button type="button">通道扩展</button>;
      },
    });
    render(<ModelSection />);
    const extra = await screen.findByRole("button", { name: "通道扩展" });
    const add = screen.getByRole("button", { name: /添加/ });
    // Wrapped with Add so the row still has exactly two children.
    expect(extra.parentElement).toBe(add.parentElement);
    expect(add.parentElement?.parentElement?.className).toContain(
      "justify-between",
    );
    expect(add.parentElement?.parentElement?.children).toHaveLength(2);
    expect(
      extra.compareDocumentPosition(add) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(typeof seen.openAdd).toBe("function");
  });

  it("openAdd opens the add-channel dialog", async () => {
    register("settings.model.channels.actions", {
      id: "test-channel-action",
      component: ({ openAdd }: { openAdd?: () => void }) => (
        <button type="button" onClick={openAdd}>
          通道扩展
        </button>
      ),
    });
    render(<ModelSection />);
    fireEvent.click(await screen.findByRole("button", { name: "通道扩展" }));
    expect(await screen.findByRole("dialog")).toBeTruthy();
  });

  it("settings.model.provider.actions renders on every channel row with provider context", async () => {
    const seen: Array<{
      id: string;
      isSystem: unknown;
      isConfigured: unknown;
    }> = [];
    register("settings.model.provider.actions", {
      id: "test-provider-action",
      component: (props: {
        provider?: { id: string };
        isSystem?: boolean;
        isConfigured?: boolean;
      }) => {
        seen.push({
          id: props.provider?.id ?? "",
          isSystem: props.isSystem,
          isConfigured: props.isConfigured,
        });
        return <button type="button">{`行动作 ${props.provider?.id}`}</button>;
      },
    });
    render(<ModelSection />);
    expect(await screen.findByText("行动作 ch-user")).toBeTruthy();
    expect(screen.getByText("行动作 ch-oauth")).toBeTruthy();
    expect(seen).toContainEqual({
      id: "ch-user",
      isSystem: false,
      isConfigured: true,
    });
    // OAuth subscription rows count as configured (the CLI holds the key).
    expect(seen).toContainEqual({
      id: "ch-oauth",
      isSystem: false,
      isConfigured: true,
    });
  });

  it("settings.section.header.actions (key model) lands beside the section title", async () => {
    register("settings.section.header.actions", {
      id: "test-model-actions",
      key: "model",
      component: () => <button type="button">模型头部动作</button>,
    });
    render(<ModelSection />);
    expect(
      await screen.findByRole("button", { name: "模型头部动作" }),
    ).toBeTruthy();
  });
});

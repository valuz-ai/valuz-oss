import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { initI18n } from "@valuz/shared/i18n";
import { dshPluginsApi, extensionsApi, useRegistryStore } from "@valuz/core";

vi.mock("@valuz/app/layout", () => ({
  useProjectOutlet: () => ({ setHideHeader: vi.fn() }),
}));

import { SettingsPage } from "./SettingsPage";

type SlotRegistrar = ReturnType<typeof useRegistryStore.getState>["registerSlot"];
type SlotRegistration = Parameters<SlotRegistrar>[1];

const register = (name: string, registration: SlotRegistration) =>
  useRegistryStore.getState().registerSlot(name, registration);

const renderGeneral = () =>
  render(
    <MemoryRouter initialEntries={["/settings?tab=general"]}>
      <SettingsPage />
    </MemoryRouter>,
  );

beforeAll(() => initI18n({ locale: "zh-CN", fallbackLocale: "zh-CN" }));
beforeEach(() => {
  localStorage.clear();
  useRegistryStore.setState({ slots: {} });
});

describe("SettingsPage slots", () => {
  it("renders only the active section in its column while every slot is empty", () => {
    renderGeneral();
    const section = screen
      .getByRole("heading", { name: "通用" })
      .closest("section");
    expect(section).not.toBeNull();
    // No header / footer wrapper or placeholder around the section.
    expect(section?.parentElement?.children).toHaveLength(1);
    // And the section's title block keeps its plain, unwrapped shape.
    expect(section?.firstElementChild?.className).toBe("mb-3");
  });

  it("settings.header renders above the section with tab and setTab", () => {
    let seen: { tab?: string; setTab?: unknown } = {};
    register("settings.header", {
      id: "test-header",
      component: (props: { tab?: string; setTab?: unknown }) => {
        seen = props;
        return <div>头部扩展</div>;
      },
    });
    renderGeneral();
    const header = screen.getByText("头部扩展");
    const section = screen
      .getByRole("heading", { name: "通用" })
      .closest("section") as HTMLElement;
    expect(
      header.compareDocumentPosition(section) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(seen.tab).toBe("general");
    expect(typeof seen.setTab).toBe("function");
  });

  it("settings.section.footer renders below the section for its own key only", () => {
    let seen: { tab?: string } = {};
    register("settings.section.footer", {
      id: "test-footer-general",
      key: "general",
      component: (props: { tab?: string }) => {
        seen = props;
        return <div>通用页脚</div>;
      },
    });
    register("settings.section.footer", {
      id: "test-footer-model",
      key: "model",
      component: () => <div>模型页脚</div>,
    });
    renderGeneral();
    const footer = screen.getByText("通用页脚");
    const section = screen
      .getByRole("heading", { name: "通用" })
      .closest("section") as HTMLElement;
    expect(
      section.compareDocumentPosition(footer) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(seen.tab).toBe("general");
    expect(screen.queryByText("模型页脚")).toBeNull();
  });

  it("settings.section.header.actions lands beside the General title, for its key only", () => {
    register("settings.section.header.actions", {
      id: "test-actions-general",
      key: "general",
      component: () => <button type="button">通用动作</button>,
    });
    register("settings.section.header.actions", {
      id: "test-actions-model",
      key: "model",
      component: () => <button type="button">模型动作</button>,
    });
    renderGeneral();
    const heading = screen.getByRole("heading", { name: "通用" });
    const action = screen.getByRole("button", { name: "通用动作" });
    expect(
      heading.compareDocumentPosition(action) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "模型动作" })).toBeNull();
  });

  it("settings.general.items renders after the onboarding card and before Shortcuts", () => {
    register("settings.general.items", {
      id: "test-general-items",
      component: () => <div>通用扩展卡片</div>,
    });
    renderGeneral();
    const onboarding = screen.getByText("新手引导");
    const item = screen.getByText("通用扩展卡片");
    const shortcuts = screen.getByText("快捷键");
    expect(
      onboarding.compareDocumentPosition(item) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      item.compareDocumentPosition(shortcuts) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});

describe("SettingsPage extensions section", () => {
  it("registers 扩展 in the system group and renders it for ?tab=extensions", async () => {
    vi.spyOn(extensionsApi, "listBackendExtensions").mockResolvedValue({
      composed: false,
      editable: true,
      plugins: [],
      config_schemas: {},
    });
    vi.spyOn(dshPluginsApi, "status").mockResolvedValue({
      enabled: false,
      available: false,
      running: false,
      unavailable_reason: "disabled",
      home: "",
      profile: "valuz",
      ui_url: null,
    });
    render(
      <MemoryRouter initialEntries={["/settings?tab=extensions"]}>
        <SettingsPage />
      </MemoryRouter>,
    );
    expect(
      useRegistryStore
        .getState()
        .settingsSections.find((s) => s.id === "extensions")?.group?.id,
    ).toBe("system");
    expect(screen.getByRole("heading", { name: "扩展" })).not.toBeNull();
    expect(screen.getByRole("heading", { name: "Valuz 扩展" })).not.toBeNull();
    expect(screen.getByRole("heading", { name: "DSH 插件" })).not.toBeNull();
    expect(await screen.findByText("DSH 插件在此环境不可用")).not.toBeNull();
  });
});

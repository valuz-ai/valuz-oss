import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { initI18n } from "@valuz/shared/i18n";
import { AppPluginOperationCard } from "./AppPluginOperationCard";
import { appPluginOperationView } from "./app-plugin-operation-view";

beforeAll(() => {
  initI18n({ locale: "zh-CN", fallbackLocale: "zh-CN" });
});
afterEach(cleanup);

const installPreview = {
  id: "acme.dashboard",
  version: "1.2.0",
  name: { "zh-CN": "Acme 看板", "en-US": "Acme Dashboard" },
  publisher: { name: "Acme" },
  source: { kind: "file", path: "/tmp/acme.zip" },
  sha256: "deadbeef",
  permissions: ["projects:read", "connectors:call"],
  added_permissions: ["connectors:call"],
  requires: ["connector:acme-data", "edition:finance"],
  unmet_requires: ["connector:acme-data"],
  has_backend: true,
  existing: { version: "1.0.0" },
};

const operation = (over: Record<string, unknown> = {}) => ({
  state: "proposed",
  operation_type: "app_plugin.install",
  preview: installPreview,
  input_payload: {},
  error_message: null,
  ...over,
});

describe("appPluginOperationView", () => {
  it("reads the action from the record type, the preview or the tool envelope", () => {
    expect(appPluginOperationView({ operation_type: "app_plugin.dev_link" }).action)
      .toBe("dev_link");
    expect(appPluginOperationView({ preview: { action: "uninstall" } }).action)
      .toBe("uninstall");
    expect(appPluginOperationView({}, "publish").action).toBe("publish");
    expect(appPluginOperationView({}).action).toBe("generic");
  });

  it("finds fields in nested preview objects and falls back to the input", () => {
    const view = appPluginOperationView({
      operation_type: "app_plugin.install",
      preview: { inspection: { sha256: "ab", permissions: ["storage"] } },
      input_payload: { manifest: undefined, source_path: "/x.zip", id: "a.b" },
    });
    expect(view).toMatchObject({
      sha256: "ab",
      permissions: ["storage"],
      path: "/x.zip",
      id: "a.b",
    });
  });

  it("copes with an empty record", () => {
    expect(appPluginOperationView({})).toMatchObject({
      id: "",
      permissions: [],
      purgeData: null,
      hasBackend: false,
    });
  });
});

describe("AppPluginOperationCard", () => {
  it("shows what an install asks for, highlighting added permissions and unmet requirements", async () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    const { container } = render(
      <AppPluginOperationCard
        operation={operation()}
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );
    expect(screen.getByText("安装并启用应用插件")).toBeTruthy();
    expect(screen.getByText("Acme 看板")).toBeTruthy();
    expect(screen.getByText("v1.2.0")).toBeTruthy();
    expect(screen.getByText("更新自 v1.0.0")).toBeTruthy();
    expect(screen.getByText("acme.dashboard")).toBeTruthy();
    expect(screen.getByText("Acme")).toBeTruthy();
    expect(screen.getByText("file · /tmp/acme.zip")).toBeTruthy();
    expect(screen.getByText("deadbeef")).toBeTruthy();
    expect(screen.getByText("带有后端服务")).toBeTruthy();
    expect(screen.getByText(/以你的权限在本机运行第三方代码/)).toBeTruthy();

    const added = container.querySelector('[data-added="true"]')!;
    expect(within(added as HTMLElement).getByText("connectors:call")).toBeTruthy();
    expect(within(added as HTMLElement).getByText("新增")).toBeTruthy();
    expect(container.querySelectorAll('[data-added="false"]')).toHaveLength(1);
    const unmet = container.querySelector('[data-unmet="true"]')!;
    expect(within(unmet as HTMLElement).getByText("connector:acme-data")).toBeTruthy();
    expect(within(unmet as HTMLElement).getByText("未满足")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "安装并启用" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("explains that a dev link is confirmed once", () => {
    render(
      <AppPluginOperationCard
        operation={operation({
          operation_type: "app_plugin.dev_link",
          preview: { id: "acme.dashboard", path: "/work/acme", permissions: [] },
        })}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText("安装并启用应用插件")).toBeTruthy();
    expect(screen.getByText("/work/acme")).toBeTruthy();
    expect(screen.getByText("未声明任何权限")).toBeTruthy();
    expect(screen.getByText(/后续构建会更新插件/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "安装并启用" })).toBeTruthy();
  });

  it("shows scope and targets for a publish", () => {
    render(
      <AppPluginOperationCard
        operation={operation({
          operation_type: "app_plugin.publish",
          preview: {
            id: "acme.dashboard",
            version: "1.2.0",
            scope: "org",
            distribution_ids: ["dist-1", "dist-2"],
            notes: "第一版",
            permissions: ["storage"],
          },
        })}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText("发布应用插件")).toBeTruthy();
    expect(screen.getByText("组织")).toBeTruthy();
    expect(screen.getByText("dist-1")).toBeTruthy();
    expect(screen.getByText("dist-2")).toBeTruthy();
    expect(screen.getByText("第一版")).toBeTruthy();
    expect(screen.getByRole("button", { name: "发布" })).toBeTruthy();
  });

  it("uses a destructive confirm for an uninstall and says whether data goes", () => {
    render(
      <AppPluginOperationCard
        operation={operation({
          operation_type: "app_plugin.uninstall",
          preview: { id: "acme.dashboard", purge_data: true },
        })}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    const confirm = screen.getByRole("button", { name: "卸载" });
    expect(confirm.getAttribute("data-variant")).toBe("destructive");
    expect(screen.getByText("同时删除应用插件数据")).toBeTruthy();
    // An uninstall has no permissions to list.
    expect(screen.queryByText("权限")).toBeNull();
  });

  it("drops the buttons once the operation is settled", () => {
    const { rerender } = render(
      <AppPluginOperationCard
        operation={operation({ state: "succeeded" })}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText("应用插件操作已完成")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    rerender(
      <AppPluginOperationCard
        operation={operation({ state: "cancelled" })}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText("已取消，未做任何修改。")).toBeTruthy();
    rerender(
      <AppPluginOperationCard
        operation={operation({ state: "failed", error_message: "磁盘已满" })}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText("磁盘已满")).toBeTruthy();
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
  });
});

import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { initI18n } from "@valuz/shared/i18n";
import { ApiError, appPluginsApi, useRegistryStore } from "@valuz/core";
import type {
  AppPluginInspection,
  AppPluginList,
  AppPlugin,
} from "@valuz/core";

// The renderer's load state comes from the ``oss-app-plugins`` runtime store.
const runtimeState = vi.hoisted(() => ({
  plugins: {} as Record<string, unknown>,
}));
vi.mock("../../../plugins/app-plugins-runtime", () => ({
  useAppPluginRuntime: () => ({
    status: "running",
    safeMode: false,
    safeModeReason: null,
    generation: 1,
    plugins: runtimeState.plugins,
  }),
}));

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
import { PlatformProvider } from "@valuz/app/platform";
import { webCapabilities } from "../../../platform/web-capabilities";
import { AppPluginBlock } from "./AppPluginBlock";

const plugin = (over: Partial<AppPlugin> = {}): AppPlugin => ({
  id: "acme.dash",
  version: "1.2.0",
  name: { "zh-CN": "Acme 看板", "en-US": "Acme Dashboard" },
  description: { "zh-CN": "在项目页显示 Acme 数据", "en-US": "Acme data" },
  publisher: { name: "Acme" },
  source: { kind: "file", path: "/tmp/acme.zip" },
  status: "enabled",
  status_reason: null,
  enabled: true,
  permissions: ["projects:read", "storage"],
  requires: [],
  unmet_requires: [],
  engines: {},
  entry_url: "/v1/app-plugin-assets/acme.dash/1/frontend/index.js",
  style_urls: [],
  locales: {},
  config_schema: null,
  automations: [],
  revision: 1,
  dev_path: null,
  sha256: "abc123",
  installed_at: 1,
  ...over,
});

const listOf = (
  plugins: AppPlugin[],
  over: Partial<AppPluginList> = {},
): AppPluginList => ({
  api_version: "1.0.0",
  safe_mode: false,
  safe_mode_reason: null,
  generation: 1,
  plugins,
  ...over,
});

const inspection = (
  over: Partial<AppPluginInspection> = {},
): AppPluginInspection => ({
  manifest: {
    id: "acme.dash",
    version: "1.2.0",
    name: { "zh-CN": "Acme 看板", "en-US": "Acme Dashboard" },
    publisher: { name: "Acme" },
  },
  sha256: "abc123",
  size: 2048,
  permissions: ["projects:read", "connectors:call"],
  requires: ["connector:acme-data"],
  unmet_requires: [],
  errors: [],
  warnings: [],
  has_backend: false,
  existing: null,
  added_permissions: [],
  ...over,
});

const never = () => new Promise<never>(() => {});

const renderBlock = async (
  plugins: AppPlugin[],
  over: Partial<AppPluginList> = {},
) => {
  const list = vi
    .spyOn(appPluginsApi, "list")
    .mockResolvedValue(listOf(plugins, over));
  const watch = vi.spyOn(appPluginsApi, "watch").mockImplementation(never);
  const view = render(<AppPluginBlock />);
  await screen.findByRole("button", { name: "从文件安装" });
  return { ...view, list, watch };
};

const rowOf = (container: HTMLElement, id: string): HTMLElement => {
  const row = container.querySelector<HTMLElement>(
    `[data-app-plugin-id="${id}"]`,
  );
  if (!row) throw new Error(`no row for ${id}`);
  return row;
};

const unregister: Array<() => void> = [];

beforeAll(() => {
  initI18n({ locale: "zh-CN", fallbackLocale: "zh-CN" });
});
beforeEach(() => {
  runtimeState.plugins = {};
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
});
afterEach(() => {
  cleanup();
  for (const fn of unregister.splice(0)) fn();
  vi.restoreAllMocks();
});

describe("AppPluginBlock — availability", () => {
  it.each([403, 404])("stays hidden when the list answers %i", async (status) => {
    const list = vi
      .spyOn(appPluginsApi, "list")
      .mockRejectedValue(new ApiError("nope", status));
    vi.spyOn(appPluginsApi, "watch").mockImplementation(never);
    const { container } = render(<AppPluginBlock />);
    await waitFor(() => expect(list).toHaveBeenCalled());
    await act(async () => {});
    expect(container.textContent).toBe("");
  });

  it("shows a retryable error when the backend cannot be read", async () => {
    const list = vi
      .spyOn(appPluginsApi, "list")
      .mockRejectedValueOnce(new Error("backend down"))
      .mockResolvedValue(listOf([]));
    vi.spyOn(appPluginsApi, "watch").mockImplementation(never);
    render(<AppPluginBlock />);
    await screen.findByText("无法读取应用插件");
    expect(screen.getByText("backend down")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("还没有安装应用插件");
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("shows the empty state", async () => {
    await renderBlock([]);
    expect(screen.getByText("还没有安装应用插件")).toBeTruthy();
  });
});

describe("AppPluginBlock — installed list", () => {
  it("renders every status with its reason and unmet requirements", async () => {
    const { container } = await renderBlock([
      plugin({ id: "p.enabled" }),
      plugin({
        id: "p.disabled",
        status: "disabled",
        enabled: false,
        name: "停用的插件",
      }),
      plugin({
        id: "p.incompat",
        status: "incompatible",
        enabled: false,
        status_reason: "需要插件 API ^2.0.0",
      }),
      plugin({
        id: "p.broken",
        status: "broken",
        status_reason: "sha256 与记录不一致",
      }),
      plugin({
        id: "p.unmet",
        status: "requires-unmet",
        unmet_requires: ["connector:acme-data"],
      }),
      plugin({
        id: "p.blocked",
        status: "blocked",
        enabled: false,
        status_reason: "组织策略不允许",
      }),
    ]);

    const expectBadge = (id: string, text: string, variant: string) => {
      const badge = within(rowOf(container, id)).getByText(text);
      expect(badge.getAttribute("data-variant")).toBe(variant);
    };
    expectBadge("p.enabled", "已启用", "success");
    expectBadge("p.disabled", "已停用", "metaNeutral");
    expectBadge("p.incompat", "不兼容", "error");
    expectBadge("p.broken", "已损坏", "error");
    expectBadge("p.unmet", "依赖未满足", "warning");
    expectBadge("p.blocked", "已被策略阻止", "error");

    const incompat = within(rowOf(container, "p.incompat"));
    expect(incompat.getByText("原因：需要插件 API ^2.0.0")).toBeTruthy();
    expect(
      within(rowOf(container, "p.unmet")).getByText(
        "未满足的依赖：connector:acme-data",
      ),
    ).toBeTruthy();
    expect(
      within(rowOf(container, "p.broken")).getByText(
        "原因：sha256 与记录不一致",
      ),
    ).toBeTruthy();
    // A plugin the backend cannot run cannot be switched on.
    for (const id of ["p.incompat", "p.blocked"]) {
      expect(
        within(rowOf(container, id)).getByRole("switch").hasAttribute("disabled"),
      ).toBe(true);
    }
    expect(
      within(rowOf(container, "p.enabled"))
        .getByRole("switch")
        .hasAttribute("disabled"),
    ).toBe(false);
  });

  it("shows the localized name, id, version, publisher, source and permissions", async () => {
    const { container } = await renderBlock([
      plugin({
        source: { kind: "catalog", scope: "org", item_id: "i1" },
        permissions: ["projects:read", "storage"],
      }),
      plugin({
        id: "dev.plugin",
        name: "Dev Plugin",
        version: "0.0.1",
        source: { kind: "dev", path: "/work/dev" },
        dev_path: "/work/dev",
        permissions: [],
      }),
    ]);
    const row = within(rowOf(container, "acme.dash"));
    expect(row.getByText("Acme 看板")).toBeTruthy();
    expect(row.getByText("acme.dash")).toBeTruthy();
    expect(row.getByText("v1.2.0")).toBeTruthy();
    expect(row.getByText("目录 · 组织")).toBeTruthy();
    expect(row.getByText("在项目页显示 Acme 数据")).toBeTruthy();
    expect(row.getByText(/发布者：Acme/)).toBeTruthy();
    expect(row.getByText("权限：projects:read, storage")).toBeTruthy();

    const dev = within(rowOf(container, "dev.plugin"));
    expect(dev.getByText("开发目录")).toBeTruthy();
    expect(dev.getByText("/work/dev")).toBeTruthy();
    expect(dev.getByText("未声明权限")).toBeTruthy();
  });

  it("shows the renderer's load state and error next to the backend status", async () => {
    runtimeState.plugins = {
      "tp.failing": { id: "tp.failing", phase: "failed", error: "boom" },
      "tp.fine": { id: "tp.fine", phase: "active" },
      "tp.off": { id: "tp.off", phase: "skipped", reason: "disabled" },
    };
    const { container } = await renderBlock([
      plugin({ id: "tp.failing" }),
      plugin({ id: "tp.fine" }),
      plugin({ id: "tp.off", enabled: false, status: "disabled" }),
    ]);
    const failing = within(rowOf(container, "tp.failing"));
    expect(failing.getByText("界面加载失败")).toBeTruthy();
    expect(failing.getByText("加载失败：boom")).toBeTruthy();
    expect(within(rowOf(container, "tp.fine")).getByText("界面已加载")).toBeTruthy();
    // A plugin the runtime knows nothing about shows the backend status only.
    expect(within(rowOf(container, "tp.off")).queryByText(/界面/)).toBeNull();
  });

  it("switches a plugin off and re-reads the list", async () => {
    const setEnabled = vi
      .spyOn(appPluginsApi, "setEnabled")
      .mockResolvedValue({ plugin: plugin({ enabled: false }) });
    const { list } = await renderBlock([plugin()]);
    await userEvent.click(
      screen.getByRole("switch", { name: "启用或停用 Acme 看板" }),
    );
    expect(setEnabled).toHaveBeenCalledWith("acme.dash", false);
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  });

  it("reports a refused toggle", async () => {
    vi.spyOn(appPluginsApi, "setEnabled").mockRejectedValue(
      new Error("policy says no"),
    );
    await renderBlock([plugin({ enabled: false, status: "disabled" })]);
    await userEvent.click(screen.getByRole("switch"));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "无法更改 Acme 看板 的状态：policy says no",
      ),
    );
  });

  it("offers reload for a dev link only", async () => {
    const reload = vi
      .spyOn(appPluginsApi, "reload")
      .mockResolvedValue({ plugin: plugin() });
    const { container } = await renderBlock([
      plugin({
        id: "dev.plugin",
        name: "Dev Plugin",
        source: { kind: "dev", path: "/w" },
      }),
      plugin(),
    ]);
    expect(
      within(rowOf(container, "acme.dash")).queryByRole("button", { name: "重载" }),
    ).toBeNull();
    await userEvent.click(
      within(rowOf(container, "dev.plugin")).getByRole("button", { name: "重载" }),
    );
    expect(reload).toHaveBeenCalledWith("dev.plugin");
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("已重载 Dev Plugin"),
    );
  });

  it("refetches when the generation moves, so outside installs appear", async () => {
    let wake: (value: { generation: number }) => void = () => {};
    const list = vi
      .spyOn(appPluginsApi, "list")
      .mockResolvedValueOnce(listOf([plugin()]))
      .mockResolvedValue(
        listOf([plugin(), plugin({ id: "cli.installed", name: "CLI 装的" })], {
          generation: 2,
        }),
      );
    const watch = vi
      .spyOn(appPluginsApi, "watch")
      .mockImplementationOnce(
        () => new Promise((resolve) => (wake = resolve)),
      )
      .mockImplementation(never);
    render(<AppPluginBlock />);
    await screen.findByText("Acme 看板");
    expect(screen.queryByText("CLI 装的")).toBeNull();
    expect(watch).toHaveBeenCalledWith(1, 25, expect.any(AbortSignal));

    await act(async () => wake({ generation: 2 }));
    await screen.findByText("CLI 装的");
    expect(list).toHaveBeenCalledTimes(2);
    // …and the next long poll asks from the new generation.
    await waitFor(() =>
      expect(watch).toHaveBeenLastCalledWith(2, 25, expect.any(AbortSignal)),
    );
  });
});

describe("AppPluginBlock — safe mode", () => {
  it("explains safe mode and leaves it, then reloads the window", async () => {
    const setSafeMode = vi
      .spyOn(appPluginsApi, "setSafeMode")
      .mockResolvedValue({ safe_mode: false });
    const windowReload = vi.fn(async () => undefined);
    vi.spyOn(appPluginsApi, "list").mockResolvedValue(
      listOf([plugin()], { safe_mode: true, safe_mode_reason: "连续加载失败" }),
    );
    vi.spyOn(appPluginsApi, "watch").mockImplementation(never);
    render(
      <PlatformProvider value={{ ...webCapabilities, windowReload }}>
        <AppPluginBlock />
      </PlatformProvider>,
    );
    await screen.findByText("安全模式");
    expect(screen.getByText("原因：连续加载失败")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "退出安全模式" }));
    await waitFor(() => expect(windowReload).toHaveBeenCalledTimes(1));
    expect(setSafeMode).toHaveBeenCalledWith(false);
    expect(setSafeMode.mock.invocationCallOrder[0]).toBeLessThan(
      windowReload.mock.invocationCallOrder[0],
    );
  });

  it("shows no banner outside safe mode", async () => {
    await renderBlock([plugin()]);
    expect(screen.queryByText("安全模式")).toBeNull();
  });
});

describe("AppPluginBlock — install", () => {
  const openFile = async () => {
    await userEvent.click(screen.getByRole("button", { name: "从文件安装" }));
    await userEvent.type(
      await screen.findByLabelText("应用插件包路径（.zip 或目录）"),
      "/tmp/acme.zip",
    );
    await userEvent.click(screen.getByRole("button", { name: "检查" }));
  };
  const risk = () =>
    screen.findByRole("checkbox", {
      name: "我了解该应用插件将以我的权限在本机运行第三方代码",
    });

  it("inspects first, shows the details, and installs only after the risk checkbox", async () => {
    const inspect = vi.spyOn(appPluginsApi, "inspect").mockResolvedValue(
      inspection({
        added_permissions: ["connectors:call"],
        unmet_requires: ["connector:acme-data"],
        warnings: ["未声明 README"],
        has_backend: true,
      }),
    );
    const install = vi.spyOn(appPluginsApi, "install").mockResolvedValue({
      plugin: plugin(),
      updated_from: null,
    });
    const { list } = await renderBlock([]);
    await openFile();

    expect(inspect).toHaveBeenCalledWith({ source_path: "/tmp/acme.zip" });
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByText("Acme 看板")).toBeTruthy();
    expect(dialog.getByText("acme.dash")).toBeTruthy();
    expect(dialog.getByText("1.2.0")).toBeTruthy();
    expect(dialog.getByText("Acme")).toBeTruthy();
    expect(dialog.getByText("本地文件 · /tmp/acme.zip")).toBeTruthy();
    expect(dialog.getByText("abc123")).toBeTruthy();
    expect(dialog.getByText("2.0 KB")).toBeTruthy();
    expect(dialog.getByText("带有后端服务")).toBeTruthy();
    expect(dialog.getByText("未声明 README")).toBeTruthy();

    // Added permissions and unmet requirements are the highlighted ones.
    const body = screen.getByRole("dialog");
    const added = body.querySelector<HTMLElement>('[data-added="true"]')!;
    expect(added.textContent).toContain("connectors:call");
    expect(added.textContent).toContain("新增");
    expect(body.querySelectorAll('[data-added="false"]')).toHaveLength(1);
    const unmet = body.querySelector<HTMLElement>('[data-unmet="true"]')!;
    expect(unmet.textContent).toContain("connector:acme-data");
    expect(unmet.textContent).toContain("未满足");

    const confirm = dialog.getByRole("button", { name: "安装" });
    expect(confirm.hasAttribute("disabled")).toBe(true);
    await userEvent.click(confirm);
    expect(install).not.toHaveBeenCalled();

    await userEvent.click(await risk());
    expect(confirm.hasAttribute("disabled")).toBe(false);
    await userEvent.click(confirm);
    await waitFor(() =>
      expect(install).toHaveBeenCalledWith(
        { source_path: "/tmp/acme.zip" },
        { expected_sha256: "abc123" },
      ),
    );
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("已安装 Acme 看板"),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // The list is re-read right away.
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  });

  it("blocks the install while the inspection has errors", async () => {
    const install = vi.spyOn(appPluginsApi, "install");
    vi.spyOn(appPluginsApi, "inspect").mockResolvedValue(
      inspection({ errors: ["清单缺少 id", "frontend.entry 不存在"] }),
    );
    await renderBlock([]);
    await openFile();
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByText("清单缺少 id")).toBeTruthy();
    expect(dialog.getByText("frontend.entry 不存在")).toBeTruthy();
    const checkbox = await risk();
    expect(checkbox.hasAttribute("disabled")).toBe(true);
    const confirm = dialog.getByRole("button", { name: "安装" });
    expect(confirm.hasAttribute("disabled")).toBe(true);
    await userEvent.click(confirm);
    expect(install).not.toHaveBeenCalled();
  });

  it("labels an update with the installed version", async () => {
    vi.spyOn(appPluginsApi, "inspect").mockResolvedValue(
      inspection({ existing: { version: "1.0.0" } }),
    );
    const install = vi.spyOn(appPluginsApi, "install").mockResolvedValue({
      plugin: plugin(),
      updated_from: "1.0.0",
    });
    await renderBlock([]);
    await openFile();
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByText("更新自 v1.0.0")).toBeTruthy();
    await userEvent.click(await risk());
    await userEvent.click(dialog.getByRole("button", { name: "更新" }));
    await waitFor(() => expect(install).toHaveBeenCalled());
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "已将 Acme 看板 从 v1.0.0 更新到 v1.2.0",
      ),
    );
  });

  it("installs from a URL", async () => {
    const inspect = vi
      .spyOn(appPluginsApi, "inspect")
      .mockResolvedValue(inspection({ size: null }));
    const install = vi.spyOn(appPluginsApi, "install").mockResolvedValue({
      plugin: plugin(),
      updated_from: null,
    });
    await renderBlock([]);
    await userEvent.click(screen.getByRole("button", { name: "从 URL 安装" }));
    await userEvent.type(
      await screen.findByLabelText("应用插件包地址"),
      "https://example.com/acme.zip",
    );
    await userEvent.click(screen.getByRole("button", { name: "检查" }));
    expect(inspect).toHaveBeenCalledWith({ url: "https://example.com/acme.zip" });
    await userEvent.click(await risk());
    await userEvent.click(screen.getByRole("button", { name: "安装" }));
    await waitFor(() =>
      expect(install).toHaveBeenCalledWith(
        { url: "https://example.com/acme.zip" },
        { expected_sha256: "abc123" },
      ),
    );
  });

  it("links a dev folder through the same inspect-and-confirm dialog", async () => {
    const inspect = vi.spyOn(appPluginsApi, "inspect").mockResolvedValue(
      inspection({ sha256: null, size: null }),
    );
    const devLink = vi
      .spyOn(appPluginsApi, "devLink")
      .mockResolvedValue({ plugin: plugin() });
    await renderBlock([]);
    await userEvent.click(screen.getByRole("button", { name: "链接开发目录" }));
    await userEvent.type(
      await screen.findByLabelText("应用插件目录"),
      "/work/acme",
    );
    await userEvent.click(screen.getByRole("button", { name: "检查" }));
    expect(inspect).toHaveBeenCalledWith({ source_path: "/work/acme" });
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByText("链接开发目录", { selector: "h2" })).toBeTruthy();
    const link = dialog.getByRole("button", { name: "链接" });
    expect(link.hasAttribute("disabled")).toBe(true);
    await userEvent.click(await risk());
    await userEvent.click(link);
    await waitFor(() => expect(devLink).toHaveBeenCalledWith("/work/acme"));
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("已链接 Acme 看板"),
    );
  });

  it("shows why an inspection failed and stays on the source step", async () => {
    vi.spyOn(appPluginsApi, "inspect").mockRejectedValue(
      new ApiError(
        "清单无效",
        400,
        JSON.stringify({ detail: { message: "清单无效", errors: ["id 格式错误"] } }),
      ),
    );
    await renderBlock([]);
    await openFile();
    expect(await screen.findByText("无法检查：清单无效")).toBeTruthy();
    expect(screen.getByText("id 格式错误")).toBeTruthy();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("shows the server's refusal when the install itself fails", async () => {
    vi.spyOn(appPluginsApi, "inspect").mockResolvedValue(inspection());
    vi.spyOn(appPluginsApi, "install").mockRejectedValue(
      new ApiError("sha256 不一致", 422),
    );
    await renderBlock([]);
    await openFile();
    await userEvent.click(await risk());
    await userEvent.click(screen.getByRole("button", { name: "安装" }));
    expect(await screen.findByText("无法安装：sha256 不一致")).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});

describe("AppPluginBlock — uninstall", () => {
  const open = async () => {
    const view = await renderBlock([plugin()]);
    await userEvent.click(
      within(rowOf(view.container, "acme.dash")).getByRole("button", {
        name: "卸载",
      }),
    );
    return { ...view, dialog: within(await screen.findByRole("dialog")) };
  };

  it("keeps the data unless asked to delete it", async () => {
    const uninstall = vi
      .spyOn(appPluginsApi, "uninstall")
      .mockResolvedValue({ removed: true, automations_deleted: 0 });
    const { dialog, list } = await open();
    expect(dialog.getByText("卸载 Acme 看板")).toBeTruthy();
    await userEvent.click(dialog.getByRole("button", { name: "卸载" }));
    await waitFor(() => expect(uninstall).toHaveBeenCalledWith("acme.dash", false));
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("已卸载 Acme 看板"),
    );
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  });

  it("passes purge_data when 同时删除数据 is ticked", async () => {
    const uninstall = vi
      .spyOn(appPluginsApi, "uninstall")
      .mockResolvedValue({ removed: true, automations_deleted: 2 });
    const { dialog } = await open();
    await userEvent.click(dialog.getByRole("checkbox", { name: "同时删除数据" }));
    await userEvent.click(dialog.getByRole("button", { name: "卸载" }));
    await waitFor(() => expect(uninstall).toHaveBeenCalledWith("acme.dash", true));
  });

  it("keeps the dialog open and says why when it fails", async () => {
    vi.spyOn(appPluginsApi, "uninstall").mockRejectedValue(new Error("文件被占用"));
    const { dialog } = await open();
    await userEvent.click(dialog.getByRole("button", { name: "卸载" }));
    expect(await dialog.findByText("无法卸载 Acme 看板：文件被占用")).toBeTruthy();
  });
});

describe("AppPluginBlock — config", () => {
  const SCHEMA = {
    type: "object",
    required: ["token"],
    properties: {
      token: { type: "string", title: { "zh-CN": "令牌", "en-US": "Token" } },
      retries: { type: "integer", default: 3 },
      verbose: { type: "boolean", default: true },
      region: { type: "string", enum: ["cn", "hk"], title: "区域" },
      tags: { type: "array", items: { type: "string" }, title: "标签" },
    },
  };

  const open = async () => {
    vi.spyOn(appPluginsApi, "getConfig").mockResolvedValue({
      values: { keep: 1 },
      schema: SCHEMA,
    });
    const view = await renderBlock([plugin({ config_schema: SCHEMA })]);
    await userEvent.click(
      within(rowOf(view.container, "acme.dash")).getByRole("button", {
        name: "配置",
      }),
    );
    return within(await screen.findByRole("dialog"));
  };

  it("offers the form only to plugins that declare settings", async () => {
    const { container } = await renderBlock([plugin()]);
    expect(
      within(rowOf(container, "acme.dash")).queryByRole("button", { name: "配置" }),
    ).toBeNull();
  });

  it("checks required fields before it writes anything", async () => {
    const put = vi.spyOn(appPluginsApi, "putConfig");
    const dialog = await open();
    await dialog.findByLabelText("令牌 *");
    await userEvent.click(dialog.getByRole("button", { name: "保存" }));
    expect(await dialog.findByText("令牌 为必填项")).toBeTruthy();
    expect(put).not.toHaveBeenCalled();
  });

  it("sends typed values, keeps unknown keys and closes on success", async () => {
    const put = vi
      .spyOn(appPluginsApi, "putConfig")
      .mockResolvedValue({ values: {}, schema: SCHEMA });
    const dialog = await open();
    await userEvent.type(await dialog.findByLabelText("令牌 *"), "abc");
    // The schema default shows up in the form.
    expect((dialog.getByLabelText("retries") as HTMLInputElement).value).toBe("3");
    await userEvent.selectOptions(dialog.getByLabelText("区域"), "hk");
    await userEvent.type(dialog.getByLabelText("标签"), "a{enter}b");
    await userEvent.click(dialog.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith("acme.dash", {
        keep: 1,
        token: "abc",
        retries: 3,
        verbose: true,
        region: "hk",
        tags: ["a", "b"],
      }),
    );
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("配置已保存"),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("rejects a non-integer before sending", async () => {
    const put = vi.spyOn(appPluginsApi, "putConfig");
    const dialog = await open();
    await userEvent.type(await dialog.findByLabelText("令牌 *"), "abc");
    const retries = dialog.getByLabelText("retries");
    await userEvent.clear(retries);
    await userEvent.type(retries, "2.5");
    await userEvent.click(dialog.getByRole("button", { name: "保存" }));
    expect(await dialog.findByText("retries 必须是整数")).toBeTruthy();
    expect(put).not.toHaveBeenCalled();
  });

  it("shows what the server rejected and stays open", async () => {
    vi.spyOn(appPluginsApi, "putConfig").mockRejectedValue(
      new ApiError(
        "配置无效",
        400,
        JSON.stringify({ detail: { message: "配置无效", errors: ["token 太短"] } }),
      ),
    );
    const dialog = await open();
    await userEvent.type(await dialog.findByLabelText("令牌 *"), "a");
    await userEvent.click(dialog.getByRole("button", { name: "保存" }));
    expect(await dialog.findByText("无法保存配置：配置无效")).toBeTruthy();
    expect(dialog.getByText("token 太短")).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});

describe("AppPluginBlock — logs", () => {
  it("lists the entries with level and source, and refreshes on demand", async () => {
    const logs = vi.spyOn(appPluginsApi, "logs").mockResolvedValue({
      entries: [
        { ts: 1_700_000_000, level: "error", message: "炸了", source: "backend" },
        { ts: 1_700_000_001, level: "info", message: "denied x", source: "audit" },
      ],
    });
    const { container } = await renderBlock([plugin()]);
    await userEvent.click(
      within(rowOf(container, "acme.dash")).getByRole("button", { name: "日志" }),
    );
    const dialog = within(await screen.findByRole("dialog"));
    expect(await dialog.findByText("炸了")).toBeTruthy();
    expect(logs).toHaveBeenCalledWith("acme.dash", 200);
    expect(dialog.getByText("error").getAttribute("data-variant")).toBe("error");
    expect(dialog.getByText("后端")).toBeTruthy();
    expect(dialog.getByText("审计")).toBeTruthy();
    await userEvent.click(dialog.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(logs).toHaveBeenCalledTimes(2));
  });

  it("says so when there are no entries or the read fails", async () => {
    const logs = vi
      .spyOn(appPluginsApi, "logs")
      .mockResolvedValueOnce({ entries: [] })
      .mockRejectedValueOnce(new Error("读不了"));
    const { container } = await renderBlock([plugin()]);
    await userEvent.click(
      within(rowOf(container, "acme.dash")).getByRole("button", { name: "日志" }),
    );
    const dialog = within(await screen.findByRole("dialog"));
    expect(await dialog.findByText("暂无日志")).toBeTruthy();
    await userEvent.click(dialog.getByRole("button", { name: "刷新" }));
    expect(await dialog.findByText("无法读取日志：读不了")).toBeTruthy();
    expect(logs).toHaveBeenCalledTimes(2);
  });
});

describe("AppPluginBlock — overlay slots", () => {
  it("draws no tab bar without overlay tabs", async () => {
    await renderBlock([plugin()]);
    expect(screen.queryByRole("tablist")).toBeNull();
  });

  it("adds a tab per keyed registration, after Installed, with { refresh }", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const seen: Array<Record<string, unknown>> = [];
    unregister.push(
      useRegistryStore.getState().registerSlot(
        "settings.plugins.app-plugins.tabs",
        {
          id: "overlay-mine",
          key: "mine",
          label: "我的扩展",
          component: (props: Record<string, unknown>) => {
            seen.push(props);
            return <p data-testid="mine-body">我的目录</p>;
          },
        },
      ),
      useRegistryStore.getState().registerSlot(
        "settings.plugins.app-plugins.tabs",
        {
          id: "overlay-org",
          key: "org",
          label: "组织扩展",
          component: () => <p data-testid="org-body">组织目录</p>,
        },
      ),
      // A repeated key — and one that shadows the Installed tab — add nothing.
      useRegistryStore.getState().registerSlot(
        "settings.plugins.app-plugins.tabs",
        {
          id: "overlay-dup",
          key: "mine",
          label: "重复",
          priority: 10,
          component: () => <p>dup</p>,
        },
      ),
      useRegistryStore.getState().registerSlot(
        "settings.plugins.app-plugins.tabs",
        {
          id: "overlay-shadow",
          key: "installed",
          label: "影子",
          priority: 10,
          component: () => <p>shadow</p>,
        },
      ),
    );

    const { list } = await renderBlock([plugin()]);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "已安装",
      "我的扩展",
      "组织扩展",
    ]);
    // Installed is the active tab and shows the list.
    expect(screen.getByText("Acme 看板")).toBeTruthy();
    expect(screen.queryByTestId("mine-body")).toBeNull();

    await userEvent.click(screen.getByRole("tab", { name: "我的扩展" }));
    expect(await screen.findByTestId("mine-body")).toBeTruthy();
    expect(typeof seen.at(-1)?.refresh).toBe("function");

    // ``refresh`` re-reads the installed list.
    await act(async () => {
      (seen.at(-1)?.refresh as () => void)();
    });
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    await userEvent.click(screen.getByRole("tab", { name: "组织扩展" }));
    expect(await screen.findByTestId("org-body")).toBeTruthy();
  });

  it("renders item actions on every row with the plugin and refresh", async () => {
    unregister.push(
      useRegistryStore.getState().registerSlot(
        "settings.plugins.app-plugins.item.actions",
        {
          id: "overlay-publish",
          component: (props: Record<string, unknown>) => (
            <button type="button" data-testid="publish">
              {`发布 ${(props.plugin as AppPlugin).id}:${typeof props.refresh}`}
            </button>
          ),
        },
      ),
    );
    const { container } = await renderBlock([
      plugin(),
      plugin({ id: "other.plugin", name: "Other" }),
    ]);
    expect(
      within(rowOf(container, "acme.dash")).getByTestId("publish").textContent,
    ).toBe("发布 acme.dash:function");
    expect(
      within(rowOf(container, "other.plugin")).getByTestId("publish").textContent,
    ).toBe("发布 other.plugin:function");
  });
});

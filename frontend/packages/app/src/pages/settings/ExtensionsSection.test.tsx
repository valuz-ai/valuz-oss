import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
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
import {
  ApiError,
  definePlugin,
  dshPluginsApi,
  extensionsApi,
  pluginHost,
} from "@valuz/core";
import type {
  DshBundleInfo,
  DshChangeResult,
  DshManagerStatus,
} from "@valuz/core";

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
import { ExtensionsSection } from "./ExtensionsSection";

const STATUS: DshManagerStatus = {
  enabled: true,
  available: true,
  running: true,
  unavailable_reason: null,
  home: "/data/dsh-home",
  profile: "valuz",
  ui_url: null,
};

const bundle = (overrides: Partial<DshBundleInfo>): DshBundleInfo => ({
  name: "some-bundle",
  enabled: true,
  installed: true,
  optional: false,
  removable: true,
  rows: [],
  overrides: [],
  ...overrides,
});

const MANAGED = [
  "@deepseek-ai/dsh-base",
  "@deepseek-ai/dsh-web-app",
  "@deepseek-ai/dsh-sdk-app",
  "valuz-dsh-bundle",
];

const applied = (
  overrides: Partial<DshChangeResult> = {},
): DshChangeResult => ({
  changed: true,
  application: "applied",
  stage: "enable",
  target: "my-bundle",
  ...overrides,
});

const rowOf = (container: HTMLElement, name: string): HTMLElement => {
  const row = container.querySelector<HTMLElement>(`[data-bundle="${name}"]`);
  if (!row) throw new Error(`no bundle row for ${name}`);
  return row;
};

const renderReady = async (bundles: DshBundleInfo[]) => {
  vi.spyOn(dshPluginsApi, "status").mockResolvedValue(STATUS);
  const listBundles = vi
    .spyOn(dshPluginsApi, "listBundles")
    .mockResolvedValue(bundles);
  const view = render(<ExtensionsSection />);
  await screen.findByText("已安装的 Bundle");
  return { ...view, listBundles };
};

beforeAll(() => initI18n({ locale: "zh-CN", fallbackLocale: "zh-CN" }));
beforeEach(() => {
  vi.restoreAllMocks();
  // A bare OSS app: the backend half of 「Valuz 扩展」 has its own tests.
  vi.spyOn(extensionsApi, "listBackendExtensions").mockResolvedValue({
    composed: false,
    editable: true,
    plugins: [],
    config_schemas: {},
  });
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
  vi.mocked(toast.warning).mockClear();
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("ExtensionsSection — Valuz extensions", () => {
  // Order matters: ``pluginHost`` is a process-wide singleton, so the empty
  // state has to be asserted before any test loads a plugin.
  it("shows an empty state while no Valuz extension is loaded", async () => {
    vi.spyOn(dshPluginsApi, "status").mockReturnValue(new Promise(() => {}));
    render(<ExtensionsSection />);
    // Let the backend half settle so its state update lands inside the test.
    await screen.findByText("此构建没有后端扩展");
    expect(screen.getByRole("heading", { name: "扩展" })).not.toBeNull();
    expect(screen.getByText("当前没有已加载的界面扩展")).not.toBeNull();
  });

  it("lists plugin-host records live with status, error and legacy count", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(dshPluginsApi, "status").mockReturnValue(new Promise(() => {}));
    await pluginHost.load(
      definePlugin({
        id: "ext-active",
        apply(ctx) {
          ctx.legacy("legacy-sidebar", () => {});
        },
      }),
    );
    await pluginHost.load(
      definePlugin({
        id: "ext-failed",
        apply() {
          throw new Error("boom");
        },
      }),
    );

    const { container } = render(<ExtensionsSection />);
    const active = container.querySelector<HTMLElement>(
      '[data-extension-id="ext-active"]',
    )!;
    expect(within(active).getByText("运行中")).not.toBeNull();
    expect(within(active).getByText("1 项旧式安装")).not.toBeNull();
    const failed = container.querySelector<HTMLElement>(
      '[data-extension-id="ext-failed"]',
    )!;
    expect(within(failed).getByText("失败")).not.toBeNull();
    expect(within(failed).getByText("加载失败：boom")).not.toBeNull();

    // subscribe(): a plugin loaded after mount appears without a remount.
    await act(async () => {
      await pluginHost.load(definePlugin({ id: "ext-late", apply() {} }));
    });
    expect(
      container.querySelector('[data-extension-id="ext-late"]'),
    ).not.toBeNull();

    // …and an unloaded one flips to the disposed status.
    await act(async () => {
      await pluginHost.unload("ext-late");
    });
    const late = container.querySelector<HTMLElement>(
      '[data-extension-id="ext-late"]',
    )!;
    expect(within(late).getByText("已卸载")).not.toBeNull();
  });
});

describe("ExtensionsSection — backend extensions", () => {
  it("lists the backend plugins under 「Valuz 扩展」, beside the UI extensions", async () => {
    vi.spyOn(dshPluginsApi, "status").mockReturnValue(new Promise(() => {}));
    vi.spyOn(extensionsApi, "listBackendExtensions").mockResolvedValue({
      composed: true,
      editable: true,
      plugins: [
        {
          id: "commercial-sites",
          status: "active",
          required: false,
          needs: [],
          provides: ["sites"],
          entitlement: null,
          error: null,
          hasConfig: false,
          desiredEnabled: true,
          boundPorts: [],
        },
      ],
      config_schemas: {},
    });
    const { container } = render(<ExtensionsSection />);
    await screen.findByText("commercial-sites");
    const valuz = screen
      .getByRole("heading", { name: "Valuz 扩展" })
      .closest("section")!;
    expect(within(valuz).getByRole("heading", { name: "界面扩展" })).not.toBeNull();
    expect(within(valuz).getByRole("heading", { name: "后端扩展" })).not.toBeNull();
    expect(
      valuz.contains(
        container.querySelector('[data-backend-extension="commercial-sites"]'),
      ),
    ).toBe(true);
  });
});

describe("ExtensionsSection — DSH plugins unavailable", () => {
  it("explains a disabled deployment (cloud) with the backend's reason, without erroring", async () => {
    vi.spyOn(dshPluginsApi, "status").mockResolvedValue({
      ...STATUS,
      enabled: false,
      available: false,
      running: false,
      unavailable_reason:
        "dsh plugin management is disabled in this deployment",
    });
    const listBundles = vi.spyOn(dshPluginsApi, "listBundles");

    render(<ExtensionsSection />);

    await screen.findByText("DSH 插件在此环境不可用");
    expect(screen.getByText(/云端和共享部署默认关闭 DSH 插件/)).not.toBeNull();
    expect(
      screen.getByText(
        "原因：dsh plugin management is disabled in this deployment",
      ),
    ).not.toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText("打开 DSH 插件管理")).toBeNull();
    // Nothing may start a host in a deployment that forbids one.
    expect(listBundles).not.toHaveBeenCalled();
  });

  it("explains an enabled but unavailable manager (no runtime closure / Node)", async () => {
    vi.spyOn(dshPluginsApi, "status").mockResolvedValue({
      ...STATUS,
      available: false,
      running: false,
      unavailable_reason: "node (>= 22.19) not found",
    });
    render(<ExtensionsSection />);
    await screen.findByText("DSH 插件在此环境不可用");
    expect(
      screen.getByText("此部署没有可用的 DSH 插件管理服务。"),
    ).not.toBeNull();
    expect(screen.getByText("原因：node (>= 22.19) not found")).not.toBeNull();
  });

  it("treats a missing status route (404) as unavailable, not as an error", async () => {
    vi.spyOn(dshPluginsApi, "status").mockRejectedValue(
      new ApiError("API 404: Not Found", 404),
    );
    render(<ExtensionsSection />);
    await screen.findByText("DSH 插件在此环境不可用");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("falls back to unavailable when starting the manager answers 409", async () => {
    vi.spyOn(dshPluginsApi, "status").mockResolvedValue(STATUS);
    vi.spyOn(dshPluginsApi, "listBundles").mockRejectedValue(
      new ApiError("manager failed to start", 409),
    );
    render(<ExtensionsSection />);
    await screen.findByText("DSH 插件在此环境不可用");
    expect(screen.getByText("原因：manager failed to start")).not.toBeNull();
  });

  it("offers a retry on an unexpected failure", async () => {
    const status = vi
      .spyOn(dshPluginsApi, "status")
      .mockRejectedValueOnce(new ApiError("API 500: kaboom", 500))
      .mockResolvedValue({ ...STATUS, enabled: false, available: false });
    render(<ExtensionsSection />);
    await screen.findByText("无法连接 DSH 插件管理服务");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("DSH 插件在此环境不可用");
    expect(status).toHaveBeenCalledTimes(2);
  });

  it("shows the connecting state while the manager host starts", async () => {
    vi.spyOn(dshPluginsApi, "status").mockResolvedValue(STATUS);
    vi.spyOn(dshPluginsApi, "listBundles").mockReturnValue(
      new Promise(() => {}),
    );
    render(<ExtensionsSection />);
    await screen.findByText(/正在连接 DSH 插件管理服务/);
    expect(screen.getAllByRole("status").length).toBeGreaterThan(0);
  });
});

describe("ExtensionsSection — DSH plugins available", () => {
  it("renders the profile line, the open-manager button and the bundle list", async () => {
    await renderReady([
      bundle({
        name: "my-bundle",
        version: "1.2.3",
        description: "Adds a tool",
      }),
    ]);
    expect(screen.getByText("valuz")).not.toBeNull();
    expect(screen.getByText("/data/dsh-home")).not.toBeNull();
    expect(
      screen.getByRole("button", { name: "打开 DSH 插件管理" }),
    ).not.toBeNull();
    expect(screen.getByText("my-bundle")).not.toBeNull();
    expect(screen.getByText("v1.2.3")).not.toBeNull();
    expect(screen.getByText("Adds a tool")).not.toBeNull();
    expect(
      screen.getByText(
        /已安装的 DSH 插件所提供的工具，在插件宿主运行期间对所有运行时可用/,
      ),
    ).not.toBeNull();
  });

  it("starts the manager and opens its token URL without an opener", async () => {
    await renderReady([]);
    const start = vi
      .spyOn(dshPluginsApi, "startManager")
      .mockResolvedValue({
        ...STATUS,
        ui_url: "http://127.0.0.1:5555/?token=abc",
      });
    const open = vi.spyOn(window, "open").mockReturnValue(null);

    fireEvent.click(screen.getByRole("button", { name: "打开 DSH 插件管理" }));

    await waitFor(() => expect(open).toHaveBeenCalled());
    expect(start).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith(
      "http://127.0.0.1:5555/?token=abc",
      "_blank",
      "noopener",
    );
  });

  it("reports a failure to start the manager instead of opening a window", async () => {
    await renderReady([]);
    vi.spyOn(dshPluginsApi, "startManager").mockRejectedValue(
      new ApiError("node not found", 409),
    );
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    fireEvent.click(screen.getByRole("button", { name: "打开 DSH 插件管理" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(open).not.toHaveBeenCalled();
  });

  it("toggles a bundle through setBundleEnabled with the bundle name and the new state", async () => {
    const { container, listBundles } = await renderReady([
      bundle({ name: "my-bundle", enabled: true }),
    ]);
    const setEnabled = vi
      .spyOn(dshPluginsApi, "setBundleEnabled")
      .mockResolvedValue(applied());
    const toggle = within(rowOf(container, "my-bundle")).getByRole("switch", {
      name: "启用 my-bundle",
    });
    expect(toggle.getAttribute("aria-checked")).toBe("true");

    fireEvent.click(toggle);

    await waitFor(() =>
      expect(setEnabled).toHaveBeenCalledWith("my-bundle", false),
    );
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("已停用 my-bundle"),
    );
    // The list is re-read so the Switch follows dsh's truth.
    await waitFor(() => expect(listBundles).toHaveBeenCalledTimes(2));
  });

  it("tells the user when a toggle needs a restart or dsh refuses it", async () => {
    const { container } = await renderReady([
      bundle({ name: "my-bundle", enabled: false }),
    ]);
    const setEnabled = vi
      .spyOn(dshPluginsApi, "setBundleEnabled")
      .mockResolvedValueOnce(applied({ application: "restart-required" }))
      .mockResolvedValueOnce(
        applied({
          application: "failed",
          error: { code: "operation-error", diagnostic: "patch is read-only" },
        }),
      );
    const toggle = () =>
      within(rowOf(container, "my-bundle")).getByRole("switch");

    fireEvent.click(toggle());
    await waitFor(() =>
      expect(toast.warning).toHaveBeenCalledWith(
        "my-bundle：变更已保存，重启 DSH 后生效",
      ),
    );
    await waitFor(() =>
      expect((toggle() as HTMLButtonElement).disabled).toBe(false),
    );

    fireEvent.click(toggle());
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "my-bundle：操作出错: patch is read-only",
      ),
    );
    expect(setEnabled).toHaveBeenCalledTimes(2);
  });

  it("locks Valuz- and dsh-managed bundles: 内置 badge, no toggle, no remove", async () => {
    const { container } = await renderReady([
      ...MANAGED.map((name) => bundle({ name, removable: true })),
      bundle({ name: "user-bundle" }),
    ]);
    for (const name of MANAGED) {
      const row = rowOf(container, name);
      expect(within(row).getByText("内置")).not.toBeNull();
      const toggle = within(row).getByRole("switch") as HTMLButtonElement;
      expect(toggle.disabled).toBe(true);
      expect(within(row).queryByRole("button", { name: /^移除/ })).toBeNull();
    }
    const user = rowOf(container, "user-bundle");
    expect(within(user).queryByText("内置")).toBeNull();
    expect(
      (within(user).getByRole("switch") as HTMLButtonElement).disabled,
    ).toBe(false);
    expect(
      within(user).getByRole("button", { name: "移除 user-bundle" }),
    ).not.toBeNull();
  });

  it("also locks a bundle dsh itself marks management-required", async () => {
    const { container } = await renderReady([
      bundle({
        name: "third-party-host",
        readOnlyReason: "management-required",
      }),
    ]);
    const row = rowOf(container, "third-party-host");
    expect(within(row).getByText("内置")).not.toBeNull();
    expect(
      (within(row).getByRole("switch") as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("does not offer removal for a bundle the installation ships, but still lets it be switched", async () => {
    const { container } = await renderReady([
      bundle({
        name: "shipped-optional",
        optional: true,
        removable: false,
        enabled: false,
      }),
    ]);
    const row = rowOf(container, "shipped-optional");
    expect(within(row).getByText("可选")).not.toBeNull();
    expect(within(row).queryByRole("button", { name: /^移除/ })).toBeNull();
    expect(
      (within(row).getByRole("switch") as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it("removes a bundle only after the confirmation dialog", async () => {
    const { container, listBundles } = await renderReady([
      bundle({ name: "my-bundle" }),
    ]);
    const remove = vi
      .spyOn(dshPluginsApi, "removeBundle")
      .mockResolvedValue(applied({ stage: "remove" }));

    fireEvent.click(
      within(rowOf(container, "my-bundle")).getByRole("button", {
        name: "移除 my-bundle",
      }),
    );
    await screen.findByText("移除 my-bundle？");
    expect(remove).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "移除" }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith("my-bundle"));
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("已移除 my-bundle"),
    );
    await waitFor(() => expect(listBundles).toHaveBeenCalledTimes(2));
  });

  it("keeps the bundle when the confirmation is cancelled", async () => {
    const { container } = await renderReady([bundle({ name: "my-bundle" })]);
    const remove = vi.spyOn(dshPluginsApi, "removeBundle");
    fireEvent.click(
      within(rowOf(container, "my-bundle")).getByRole("button", {
        name: "移除 my-bundle",
      }),
    );
    await screen.findByText("移除 my-bundle？");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() =>
      expect(screen.queryByText("移除 my-bundle？")).toBeNull(),
    );
    expect(remove).not.toHaveBeenCalled();
  });
});

describe("ExtensionsSection — DSH install flow", () => {
  const typeSpec = (value: string) =>
    fireEvent.change(screen.getByLabelText("安装来源"), {
      target: { value },
    });

  it("inspects first, then installs only on confirmation, and refreshes the list", async () => {
    const { listBundles } = await renderReady([]);
    const inspect = vi.spyOn(dshPluginsApi, "inspect").mockResolvedValue({
      status: "accepted",
      kind: "registry",
      bundle: true,
      registry: null,
      name: "@scope/pkg",
      version: "1.4.0",
      description: "A plugin bundle",
    });
    const install = vi.spyOn(dshPluginsApi, "installBundle").mockResolvedValue({
      changed: true,
      application: "applied",
      stage: "enable",
      target: "@scope/pkg",
      bundle: "@scope/pkg",
      version: "1.4.0",
    });

    const inspectButton = screen.getByRole("button", { name: "检查" });
    expect((inspectButton as HTMLButtonElement).disabled).toBe(true);
    typeSpec("  @scope/pkg@^1  ");
    fireEvent.click(inspectButton);

    await screen.findByText("可以安装");
    expect(inspect).toHaveBeenCalledWith("@scope/pkg@^1");
    expect(install).not.toHaveBeenCalled();
    expect(screen.getByText("npm 包")).not.toBeNull();
    expect(screen.getByText("@scope/pkg@1.4.0")).not.toBeNull();
    expect(screen.getByText("A plugin bundle")).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "安装" }));

    await screen.findByText("已安装并启用 @scope/pkg@1.4.0");
    expect(install).toHaveBeenCalledWith("@scope/pkg@^1", { enabled: true });
    expect(inspect.mock.invocationCallOrder[0]).toBeLessThan(
      install.mock.invocationCallOrder[0]!,
    );
    await waitFor(() => expect(listBundles).toHaveBeenCalledTimes(2));
    expect((screen.getByLabelText("安装来源") as HTMLInputElement).value).toBe(
      "",
    );
  });

  it("shows the restart-required notice for an install dsh cannot hot-apply", async () => {
    await renderReady([]);
    vi.spyOn(dshPluginsApi, "inspect").mockResolvedValue({
      status: "accepted",
      kind: "path",
      bundle: true,
      registry: null,
      name: "local-bundle",
    });
    vi.spyOn(dshPluginsApi, "installBundle").mockResolvedValue({
      changed: true,
      application: "restart-required",
      stage: "enable",
      target: "/abs/local-bundle",
      bundle: "local-bundle",
    });
    typeSpec("/abs/local-bundle");
    fireEvent.click(screen.getByRole("button", { name: "检查" }));
    await screen.findByText("可以安装");
    fireEvent.click(screen.getByRole("button", { name: "安装" }));
    await screen.findByText("已安装 local-bundle，重启 DSH 后生效");
  });

  it("explains a refused inspection and offers no install", async () => {
    await renderReady([]);
    vi.spyOn(dshPluginsApi, "inspect").mockResolvedValue({
      status: "refused",
      problem: "not-a-bundle",
      reason: "left-pad declares no dsh.bundle",
    });
    typeSpec("left-pad");
    fireEvent.click(screen.getByRole("button", { name: "检查" }));
    await screen.findByText("无法安装");
    expect(screen.getByText("不是 DSH bundle")).not.toBeNull();
    expect(screen.getByText("left-pad declares no dsh.bundle")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "安装" })).toBeNull();
  });

  it("surfaces a failed install with the error and a collapsible pnpm output", async () => {
    const { listBundles } = await renderReady([]);
    vi.spyOn(dshPluginsApi, "inspect").mockResolvedValue({
      status: "accepted",
      kind: "git",
      bundle: null,
      registry: null,
      host: "github.com",
    });
    vi.spyOn(dshPluginsApi, "installBundle").mockResolvedValue({
      changed: false,
      application: "failed",
      stage: "install",
      target: "github:acme/plugin",
      error: { code: "operation-error", diagnostic: "pnpm exited with 1" },
      pendingBuilds: ["esbuild"],
      packageResult: {
        exitCode: 1,
        output: "ERR_PNPM_FETCH_404 not found",
        truncated: false,
        logPath: "/tmp/pnpm.log",
      },
    });
    typeSpec("github:acme/plugin");
    fireEvent.click(screen.getByRole("button", { name: "检查" }));
    await screen.findByText("可以安装");
    expect(screen.getByText("来源：github.com")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "安装" }));

    await screen.findByText("安装失败");
    expect(screen.getByText("pnpm exited with 1")).not.toBeNull();
    expect(
      screen.getByText(/需要先批准这些包的构建脚本：esbuild/),
    ).not.toBeNull();
    // The output is one click away, not dumped on screen.
    expect(screen.queryByText("ERR_PNPM_FETCH_404 not found")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "安装输出" }));
    const pre = await screen.findByText("ERR_PNPM_FETCH_404 not found");
    expect(pre.tagName).toBe("PRE");
    // A failed install changes nothing, so the list is not re-read.
    expect(listBundles).toHaveBeenCalledTimes(1);
  });

  it("reports an inspect transport failure inline", async () => {
    await renderReady([]);
    vi.spyOn(dshPluginsApi, "inspect").mockRejectedValue(
      new ApiError("manager unreachable", 409),
    );
    typeSpec("some-package");
    fireEvent.click(screen.getByRole("button", { name: "检查" }));
    await screen.findByText("检查失败：manager unreachable");
  });

  it("forgets a stale inspection when the spec is edited", async () => {
    await renderReady([]);
    vi.spyOn(dshPluginsApi, "inspect").mockResolvedValue({
      status: "accepted",
      kind: "registry",
      bundle: true,
      registry: null,
      name: "a",
    });
    typeSpec("a");
    fireEvent.click(screen.getByRole("button", { name: "检查" }));
    await screen.findByText("可以安装");
    typeSpec("b");
    expect(screen.queryByText("可以安装")).toBeNull();
    expect(screen.queryByRole("button", { name: "安装" })).toBeNull();
  });
});

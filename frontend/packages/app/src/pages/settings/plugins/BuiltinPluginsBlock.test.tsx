import {
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
import { ApiError, builtinPluginsApi } from "@valuz/core";
import type { BuiltinPluginList, BuiltinPluginRow } from "@valuz/core";

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
import { BuiltinPluginsBlock } from "./BuiltinPluginsBlock";

const row = (overrides: Partial<BuiltinPluginRow>): BuiltinPluginRow => ({
  id: "some-plugin",
  status: "active",
  required: false,
  needs: [],
  provides: [],
  entitlement: null,
  error: null,
  hasConfig: false,
  desiredEnabled: true,
  boundPorts: [],
  ...overrides,
});

const listOf = (
  plugins: BuiltinPluginRow[],
  overrides: Partial<BuiltinPluginList> = {},
): BuiltinPluginList => ({
  composed: true,
  editable: true,
  plugins,
  config_schemas: {},
  ...overrides,
});

const rowEl = (container: HTMLElement, id: string): HTMLElement => {
  const el = container.querySelector<HTMLElement>(
    `[data-builtin-backend-plugin="${id}"]`,
  );
  if (!el) throw new Error(`no built-in backend plugin row for ${id}`);
  return el;
};

const switchOf = (container: HTMLElement, id: string) =>
  within(rowEl(container, id)).getByRole("switch") as HTMLButtonElement;

const renderBlock = async (list: BuiltinPluginList) => {
  const listBackend = vi
    .spyOn(builtinPluginsApi, "listBuiltinPlugins")
    .mockResolvedValue(list);
  const view = render(<BuiltinPluginsBlock />);
  // The heading is there from the first render; wait for the data to land.
  await waitFor(() => expect(listBackend).toHaveBeenCalled());
  // The restart hint is a ``status`` too, so wait for the loader specifically.
  await waitFor(() =>
    expect(screen.queryByRole("status", { name: "Loading" })).toBeNull(),
  );
  return { ...view, listBackend };
};

const RESTART_HINT = "有变更已记录，重启 Valuz 后生效。";

beforeAll(() => initI18n({ locale: "zh-CN", fallbackLocale: "zh-CN" }));
beforeEach(() => {
  vi.restoreAllMocks();
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("BuiltinPluginsBlock — list", () => {
  it("renders one row per plugin with id, status tag and provides / needs / entitlement", async () => {
    const { container } = await renderBlock(
      listOf([
        row({
          id: "commercial-sites",
          provides: ["sites", "publish"],
          needs: ["auth"],
          entitlement: "sites_pro",
        }),
        row({
          id: "broken-plugin",
          status: "failed",
          error: "ImportError: no module named x",
        }),
        row({ id: "idle-plugin", status: "disabled", desiredEnabled: false }),
        row({
          id: "unmet-plugin",
          status: "failed",
          error: "need 'billing' is unavailable",
        }),
        row({ id: "waiting-plugin", status: "pending" }),
      ]),
    );

    expect(screen.getByRole("heading", { name: "后端插件" })).not.toBeNull();

    const sites = rowEl(container, "commercial-sites");
    expect(within(sites).getByText("commercial-sites")).not.toBeNull();
    expect(within(sites).getByText("运行中")).not.toBeNull();
    expect(
      within(sites).getByText(
        "提供：sites, publish · 依赖：auth · 权益：sites_pro",
      ),
    ).not.toBeNull();

    const broken = rowEl(container, "broken-plugin");
    expect(within(broken).getByText("失败")).not.toBeNull();
    expect(
      within(broken).getByText("加载失败：ImportError: no module named x"),
    ).not.toBeNull();

    expect(
      within(rowEl(container, "idle-plugin")).getByText("已停用"),
    ).not.toBeNull();
    expect(
      within(rowEl(container, "unmet-plugin")).getByText(
        "加载失败：need 'billing' is unavailable",
      ),
    ).not.toBeNull();
    expect(
      within(rowEl(container, "waiting-plugin")).getByText("等待启动"),
    ).not.toBeNull();

    // Meta is shown only where there is something to say.
    expect(within(broken).queryByText(/提供：|依赖：|权益：/)).toBeNull();
    // Nothing is waiting for a restart yet.
    expect(screen.queryByText(RESTART_HINT)).toBeNull();
    expect(screen.queryByText(/已记录，重启后/)).toBeNull();
  });

  it("binds each Switch to the persisted desire", async () => {
    const { container } = await renderBlock(
      listOf([
        row({ id: "on", desiredEnabled: true }),
        row({ id: "off", status: "disabled", desiredEnabled: false }),
      ]),
    );
    expect(switchOf(container, "on").getAttribute("aria-checked")).toBe("true");
    expect(switchOf(container, "off").getAttribute("aria-checked")).toBe(
      "false",
    );
  });

  it("does not flag a plugin that was switched off at start as pending (status disposed)", async () => {
    // Hosts that have not split "disabled" out report it as disposed.
    const { container } = await renderBlock(
      listOf([
        row({
          id: "off-at-boot",
          status: "disposed",
          error: "disabled",
          desiredEnabled: false,
        }),
      ]),
    );
    const el = rowEl(container, "off-at-boot");
    expect(within(el).getByText("已停用")).not.toBeNull();
    // The magic ``error: "disabled"`` is not an error message.
    expect(within(el).queryByText(/加载失败|已跳过：/)).toBeNull();
    expect(screen.queryByText(RESTART_HINT)).toBeNull();
    expect(within(el).queryByText(/已记录，重启后/)).toBeNull();
  });

  it("flags a recorded change that this boot has not applied, in both directions", async () => {
    const { container } = await renderBlock(
      listOf([
        row({ id: "going-off", status: "active", desiredEnabled: false }),
        row({ id: "coming-on", status: "disabled", desiredEnabled: true }),
        row({ id: "steady", status: "active", desiredEnabled: true }),
      ]),
    );
    expect(
      within(rowEl(container, "going-off")).getByText("已记录，重启后停用"),
    ).not.toBeNull();
    expect(
      within(rowEl(container, "coming-on")).getByText("已记录，重启后启用"),
    ).not.toBeNull();
    expect(
      within(rowEl(container, "steady")).queryByText(/已记录，重启后/),
    ).toBeNull();
    expect(screen.getByText(RESTART_HINT)).not.toBeNull();
  });
});

describe("BuiltinPluginsBlock — required plugins", () => {
  it("locks a required plugin on, with a 必需 tag, and never calls the API", async () => {
    const { container } = await renderBlock(
      listOf([row({ id: "core", required: true }), row({ id: "optional" })]),
    );
    const set = vi.spyOn(builtinPluginsApi, "setBuiltinPluginEnabled");

    const required = rowEl(container, "core");
    expect(within(required).getByText("必需")).not.toBeNull();
    const toggle = switchOf(container, "core");
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    expect(toggle.disabled).toBe(true);
    fireEvent.click(toggle);
    expect(set).not.toHaveBeenCalled();

    const optional = rowEl(container, "optional");
    expect(within(optional).queryByText("必需")).toBeNull();
    expect(switchOf(container, "optional").disabled).toBe(false);
  });

  it("keeps a required plugin on even if the stored desire says off", async () => {
    const { container } = await renderBlock(
      listOf([row({ id: "core", required: true, desiredEnabled: false })]),
    );
    expect(switchOf(container, "core").getAttribute("aria-checked")).toBe(
      "true",
    );
    // A required plugin cannot be toggled, so it is never "pending".
    expect(screen.queryByText(RESTART_HINT)).toBeNull();
  });
});

describe("BuiltinPluginsBlock — locked by a dependent", () => {
  it("locks a plugin a required plugin needs, and says who needs it instead of 必需", async () => {
    const { container } = await renderBlock(
      listOf([
        row({ id: "oss-agents", required: true }),
        row({ id: "oss-tasks", requiredBy: ["oss-agents"] }),
        row({ id: "oss-browser", requiredBy: [] }),
      ]),
    );
    const set = vi.spyOn(builtinPluginsApi, "setBuiltinPluginEnabled");

    const locked = rowEl(container, "oss-tasks");
    expect(within(locked).getByText("被 oss-agents 依赖")).not.toBeNull();
    expect(within(locked).queryByText("必需")).toBeNull();
    const toggle = switchOf(container, "oss-tasks");
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    expect(toggle.disabled).toBe(true);
    fireEvent.click(toggle);
    expect(set).not.toHaveBeenCalled();

    // A required plugin keeps 必需 even when it is also listed as required-by.
    expect(
      within(rowEl(container, "oss-agents")).getByText("必需"),
    ).not.toBeNull();

    // An empty requiredBy locks nothing.
    const free = rowEl(container, "oss-browser");
    expect(within(free).queryByText(/依赖/)).toBeNull();
    expect(switchOf(container, "oss-browser").disabled).toBe(false);
  });

  it("names every dependent, and keeps a locked plugin on whatever the stored desire says", async () => {
    const { container } = await renderBlock(
      listOf([
        row({
          id: "oss-memory",
          requiredBy: ["oss-core", "oss-agents"],
          desiredEnabled: false,
        }),
      ]),
    );

    expect(
      within(rowEl(container, "oss-memory")).getByText(
        "被 oss-core, oss-agents 依赖",
      ),
    ).not.toBeNull();
    expect(switchOf(container, "oss-memory").getAttribute("aria-checked")).toBe(
      "true",
    );
    // Locked rows are never "pending a restart".
    expect(screen.queryByText(RESTART_HINT)).toBeNull();
  });

  it("treats a backend that predates requiredBy as unlocked", async () => {
    const { container } = await renderBlock(
      listOf([row({ id: "legacy", requiredBy: undefined })]),
    );

    expect(switchOf(container, "legacy").disabled).toBe(false);
  });
});

describe("BuiltinPluginsBlock — read-only deployment", () => {
  it("disables every Switch and explains why, once", async () => {
    const { container } = await renderBlock(
      listOf(
        [
          row({ id: "a" }),
          row({ id: "b", status: "disabled", desiredEnabled: false }),
        ],
        {
          editable: false,
        },
      ),
    );
    const set = vi.spyOn(builtinPluginsApi, "setBuiltinPluginEnabled");

    expect(
      screen.getAllByText("此部署的插件由运营方统一管理，无法在这里更改。"),
    ).toHaveLength(1);
    for (const id of ["a", "b"]) {
      const toggle = switchOf(container, id);
      expect(toggle.disabled).toBe(true);
      fireEvent.click(toggle);
    }
    expect(set).not.toHaveBeenCalled();
    // Still a full list of what is running.
    expect(within(rowEl(container, "a")).getByText("运行中")).not.toBeNull();
  });

  it("does not show the read-only line on an editable deployment", async () => {
    await renderBlock(listOf([row({ id: "a" })]));
    expect(screen.queryByText(/由运营方统一管理/)).toBeNull();
  });
});

describe("BuiltinPluginsBlock — toggling", () => {
  it("records the toggle, then shows the row notice and the block-level restart hint", async () => {
    const { container, listBackend } = await renderBlock(
      listOf([row({ id: "commercial-sites" }), row({ id: "other" })]),
    );
    const set = vi
      .spyOn(builtinPluginsApi, "setBuiltinPluginEnabled")
      .mockResolvedValue({ application: "restart-required" });

    fireEvent.click(switchOf(container, "commercial-sites"));

    await waitFor(() =>
      expect(set).toHaveBeenCalledWith("commercial-sites", false),
    );
    expect(
      await within(rowEl(container, "commercial-sites")).findByText(
        "已记录，重启后停用",
      ),
    ).not.toBeNull();
    expect(screen.getByText(RESTART_HINT)).not.toBeNull();
    expect(
      switchOf(container, "commercial-sites").getAttribute("aria-checked"),
    ).toBe("false");
    // The untouched row stays quiet; the status badge still says what is running.
    expect(
      within(rowEl(container, "other")).queryByText(/已记录，重启后/),
    ).toBeNull();
    expect(
      within(rowEl(container, "commercial-sites")).getByText("运行中"),
    ).not.toBeNull();
    expect(toast.error).not.toHaveBeenCalled();
    // The switch frees up again afterwards.
    await waitFor(() =>
      expect(switchOf(container, "other").disabled).toBe(false),
    );
    expect(listBackend).toHaveBeenCalledTimes(1);
  });

  it("flips a disabled plugin on with 重启后启用", async () => {
    const { container } = await renderBlock(
      listOf([row({ id: "idle", status: "disabled", desiredEnabled: false })]),
    );
    const set = vi
      .spyOn(builtinPluginsApi, "setBuiltinPluginEnabled")
      .mockResolvedValue({ application: "restart-required" });

    fireEvent.click(switchOf(container, "idle"));

    await waitFor(() => expect(set).toHaveBeenCalledWith("idle", true));
    expect(
      await within(rowEl(container, "idle")).findByText("已记录，重启后启用"),
    ).not.toBeNull();
  });

  it("keeps the notice after a toggle back, since a change was recorded this session", async () => {
    const { container } = await renderBlock(listOf([row({ id: "p" })]));
    const set = vi
      .spyOn(builtinPluginsApi, "setBuiltinPluginEnabled")
      .mockResolvedValue({ application: "restart-required" });

    fireEvent.click(switchOf(container, "p"));
    await waitFor(() => expect(set).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(switchOf(container, "p").disabled).toBe(false));
    fireEvent.click(switchOf(container, "p"));
    await waitFor(() => expect(set).toHaveBeenCalledTimes(2));

    expect(set).toHaveBeenLastCalledWith("p", true);
    expect(await screen.findByText(RESTART_HINT)).not.toBeNull();
  });

  it("shows the server detail in a toast on a 409 and changes nothing", async () => {
    const { container, listBackend } = await renderBlock(
      listOf([row({ id: "core" })]),
    );
    vi.spyOn(builtinPluginsApi, "setBuiltinPluginEnabled").mockRejectedValue(
      new ApiError("'core' is required", 409),
    );

    fireEvent.click(switchOf(container, "core"));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("core：'core' is required"),
    );
    expect(switchOf(container, "core").getAttribute("aria-checked")).toBe(
      "true",
    );
    expect(screen.queryByText(RESTART_HINT)).toBeNull();
    // The refusal means the screen may be stale, so it is re-read in place.
    await waitFor(() => expect(listBackend).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(switchOf(container, "core").disabled).toBe(false),
    );
  });

  it("shows the server detail on a 403 and switches the block to read-only after the re-read", async () => {
    const { container, listBackend } = await renderBlock(
      listOf([row({ id: "p" })]),
    );
    vi.spyOn(builtinPluginsApi, "setBuiltinPluginEnabled").mockRejectedValue(
      new ApiError("built-in plugins are managed by the operator here", 403),
    );
    listBackend.mockResolvedValue(
      listOf([row({ id: "p" })], { editable: false }),
    );

    fireEvent.click(switchOf(container, "p"));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "p：built-in plugins are managed by the operator here",
      ),
    );
    await screen.findByText("此部署的插件由运营方统一管理，无法在这里更改。");
    expect(switchOf(container, "p").disabled).toBe(true);
    expect(screen.queryByText(RESTART_HINT)).toBeNull();
  });

  it("reports a non-HTTP failure with its message", async () => {
    const { container } = await renderBlock(listOf([row({ id: "p" })]));
    vi.spyOn(builtinPluginsApi, "setBuiltinPluginEnabled").mockRejectedValue(
      new TypeError("Failed to fetch"),
    );
    fireEvent.click(switchOf(container, "p"));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("p：Failed to fetch"),
    );
  });
});

describe("BuiltinPluginsBlock — states", () => {
  it("shows a short 'no built-in backend plugins' state, not an error, when no plugin host is composed", async () => {
    const { container } = await renderBlock(listOf([], { composed: false }));
    expect(screen.getByText("此构建没有后端插件")).not.toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(container.querySelector('[role="switch"]')).toBeNull();
    expect(screen.queryByText(RESTART_HINT)).toBeNull();
  });

  it("says so when a plugin host composed nothing", async () => {
    await renderBlock(listOf([]));
    expect(screen.getByText("后端没有装配任何插件")).not.toBeNull();
  });

  it("treats a missing route (404) as 'no built-in backend plugins'", async () => {
    vi.spyOn(builtinPluginsApi, "listBuiltinPlugins").mockRejectedValue(
      new ApiError("API 404: Not Found", 404),
    );
    render(<BuiltinPluginsBlock />);
    await screen.findByText("此构建没有后端插件");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows a loading state while the list is read", () => {
    vi.spyOn(builtinPluginsApi, "listBuiltinPlugins").mockReturnValue(
      new Promise(() => {}),
    );
    render(<BuiltinPluginsBlock />);
    expect(screen.getByRole("status", { name: "Loading" })).not.toBeNull();
    expect(screen.getByRole("heading", { name: "后端插件" })).not.toBeNull();
  });

  it("offers a retry when the list cannot be read", async () => {
    const listBackend = vi
      .spyOn(builtinPluginsApi, "listBuiltinPlugins")
      .mockRejectedValueOnce(new ApiError("API 500: kaboom", 500))
      .mockResolvedValue(listOf([row({ id: "recovered" })]));
    render(<BuiltinPluginsBlock />);

    await screen.findByText("无法读取后端插件");
    expect(screen.getByText("API 500: kaboom")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    await screen.findByText("recovered");
    expect(listBackend).toHaveBeenCalledTimes(2);
  });
});

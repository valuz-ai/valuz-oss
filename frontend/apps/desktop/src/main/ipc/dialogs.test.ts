import { beforeEach, describe, expect, it, vi } from "vitest";

// Electron 43+ opens a dialog with no defaultPath in Downloads and stops the OS
// from restoring the last folder, so ``registerIpcHandlers`` remembers it.
// ``vi.mock`` is hoisted above the imports, so the fakes live in this holder.
const fake = vi.hoisted(() => ({
  handlers: new Map<string, (...args: unknown[]) => unknown>(),
  showOpenDialog: vi.fn(),
  showSaveDialog: vi.fn(),
  copyFile: vi.fn(async () => undefined),
}));

vi.mock("electron", () => ({
  app: {},
  dialog: {
    showOpenDialog: fake.showOpenDialog,
    showSaveDialog: fake.showSaveDialog,
  },
  ipcMain: {
    handle: (channel: string, handler: (...args: unknown[]) => unknown) => {
      fake.handlers.set(channel, handler);
    },
  },
  shell: {},
}));
vi.mock("node:fs/promises", async (importOriginal) => ({
  ...(await importOriginal<typeof import("node:fs/promises")>()),
  copyFile: fake.copyFile,
}));
vi.mock("../windows", () => ({ getMainWindow: () => null }));
vi.mock("../security", () => ({ openExternalIfSafe: vi.fn() }));
vi.mock("./desktop", () => ({ desktopRuntime: {} }));
vi.mock("./notifications", () => ({ registerNotificationHandlers: vi.fn() }));
vi.mock("./services", () => ({ serviceHandlers: () => ({}) }));
vi.mock("./cli-login", () => ({
  getCliStatus: vi.fn(),
  launchTerminalWithCommand: vi.fn(),
}));
vi.mock("./install-cli", () => ({
  getCliInstallStatus: vi.fn(),
  installCliToPath: vi.fn(),
  uninstallCliFromPath: vi.fn(),
}));

/** A fresh module per test: the remembered folder is module state. */
const register = async () => {
  vi.resetModules();
  fake.handlers.clear();
  const { registerIpcHandlers } = await import("./index");
  registerIpcHandlers();
  return (channel: string, args?: unknown) =>
    fake.handlers.get(channel)?.({}, args);
};

beforeEach(() => {
  fake.showOpenDialog.mockReset();
  fake.showSaveDialog.mockReset();
});

describe("dialog folder memory", () => {
  it("opens the first folder picker without a defaultPath", async () => {
    const invoke = await register();
    fake.showOpenDialog.mockResolvedValue({ canceled: true, filePaths: [] });
    await invoke("select_directory");
    expect(fake.showOpenDialog.mock.calls[0][0]).not.toHaveProperty(
      "defaultPath",
    );
  });

  it("reopens the folder picker where the last pick was made", async () => {
    const invoke = await register();
    fake.showOpenDialog.mockResolvedValue({
      canceled: false,
      filePaths: ["/work/reports/q3"],
    });
    expect(await invoke("select_directory")).toEqual({
      canceled: false,
      path: "/work/reports/q3",
    });
    await invoke("select_directory");
    expect(fake.showOpenDialog.mock.calls[1][0]).toMatchObject({
      defaultPath: "/work/reports",
    });
  });

  it("keeps the remembered folder when a pick is cancelled", async () => {
    const invoke = await register();
    fake.showOpenDialog.mockResolvedValueOnce({
      canceled: false,
      filePaths: ["/work/reports/q3"],
    });
    fake.showOpenDialog.mockResolvedValueOnce({
      canceled: true,
      filePaths: [],
    });
    fake.showOpenDialog.mockResolvedValueOnce({
      canceled: true,
      filePaths: [],
    });
    await invoke("select_directory");
    await invoke("select_directory");
    await invoke("select_directory");
    expect(fake.showOpenDialog.mock.calls[2][0]).toMatchObject({
      defaultPath: "/work/reports",
    });
  });

  it("shares the folder between save and pick, keeping the suggested name", async () => {
    const invoke = await register();
    fake.showSaveDialog.mockResolvedValue({
      canceled: false,
      filePath: "/exports/deck.pptx",
    });
    await invoke("save_file_as", { path: "/tmp/src/deck.pptx" });
    expect(fake.showSaveDialog.mock.calls[0][0]).toEqual({
      defaultPath: "deck.pptx",
    });

    await invoke("save_file_as", {
      path: "/tmp/src/notes.md",
      suggestedName: "notes-final.md",
    });
    expect(fake.showSaveDialog.mock.calls[1][0]).toEqual({
      defaultPath: "/exports/notes-final.md",
    });

    fake.showOpenDialog.mockResolvedValue({ canceled: true, filePaths: [] });
    await invoke("select_directory");
    expect(fake.showOpenDialog.mock.calls[0][0]).toMatchObject({
      defaultPath: "/exports",
    });
  });
});

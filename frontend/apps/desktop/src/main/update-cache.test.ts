/**
 * The purge exists because a finished install leaves electron-updater's ~600 MB
 * package behind. It also used to delete ``update.zip`` — the fixed-name copy
 * electron-updater diffs the *next* release against — so every update fell back
 * to a full download ("Unable to locate previous update.zip for differential
 * download"). These pin both halves: what goes, and what must stay.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const fsMock = vi.hoisted(() => ({
  existsSync: vi.fn(),
  readFileSync: vi.fn(),
  readdirSync: vi.fn(),
  rmSync: vi.fn(),
}));

vi.mock("node:fs", () => ({ ...fsMock, default: fsMock }));
vi.mock("electron", () => ({
  app: { isPackaged: true, getPath: () => "/virtual/home" },
}));

const { cleanStaleUpdateCache } = await import("./update-cache");

const CACHE_DIR_NAME = "@valuzdesktop-updater";

const removedNames = () =>
  fsMock.rmSync.mock.calls.map((call) => String(call[0]).split("/").pop());

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(process, "resourcesPath", {
    value: "/virtual/Resources",
    configurable: true,
  });
  fsMock.existsSync.mockReturnValue(true);
  fsMock.readFileSync.mockReturnValue(
    `provider: generic\nurl: https://files.valuz.cn/oss/\nupdaterCacheDirName: '${CACHE_DIR_NAME}'\n`,
  );
});

describe("cleanStaleUpdateCache", () => {
  it("deletes the staged package, the versioned download and the state file", () => {
    fsMock.readdirSync.mockReturnValue([
      "pending",
      "valuz-oss-v0.3.4-darwin-arm64.zip",
      "valuz-oss-v0.3.4-darwin-arm64.zip.blockmap",
      "update-info.json",
    ]);

    cleanStaleUpdateCache();

    expect(removedNames().sort()).toEqual([
      "pending",
      "update-info.json",
      "valuz-oss-v0.3.4-darwin-arm64.zip",
      "valuz-oss-v0.3.4-darwin-arm64.zip.blockmap",
    ]);
  });

  it("keeps the differential-download state every platform diffs against", () => {
    // MacUpdater copies the finished download to ``update.zip``; NsisUpdater
    // uses ``installer.exe`` / ``package.7z``. Deleting these is what turned
    // every update into a full download.
    fsMock.readdirSync.mockReturnValue([
      "update.zip",
      "installer.exe",
      "package.7z",
    ]);

    cleanStaleUpdateCache();

    expect(fsMock.rmSync).not.toHaveBeenCalled();
  });

  it("keeps the state file whatever case the platform reports", () => {
    fsMock.readdirSync.mockReturnValue(["Update.zip", "Installer.exe"]);

    cleanStaleUpdateCache();

    expect(fsMock.rmSync).not.toHaveBeenCalled();
  });

  it("deletes installer packages for every platform artifact type", () => {
    fsMock.readdirSync.mockReturnValue([
      "valuz-oss-0.3.4.dmg",
      "valuz-oss-0.3.4.exe",
      "valuz-oss-0.3.4.AppImage",
      "valuz-oss-0.3.4-full.nupkg",
    ]);

    cleanStaleUpdateCache();

    expect(fsMock.rmSync).toHaveBeenCalledTimes(4);
  });

  it("leaves anything that is not an update artifact alone", () => {
    fsMock.readdirSync.mockReturnValue(["installer.log", "some-dir"]);

    cleanStaleUpdateCache();

    expect(fsMock.rmSync).not.toHaveBeenCalled();
  });

  it("resolves the cache directory from app-update.yml, not a hardcoded name", () => {
    fsMock.readdirSync.mockReturnValue(["valuz-oss-0.3.4.dmg"]);

    cleanStaleUpdateCache();

    expect(String(fsMock.rmSync.mock.calls[0][0])).toContain(CACHE_DIR_NAME);
  });

  it("does nothing when app-update.yml is missing", () => {
    fsMock.existsSync.mockReturnValue(false);

    cleanStaleUpdateCache();

    expect(fsMock.readdirSync).not.toHaveBeenCalled();
    expect(fsMock.rmSync).not.toHaveBeenCalled();
  });

  it("swallows filesystem errors rather than break startup", () => {
    fsMock.readdirSync.mockReturnValue(["valuz-oss-0.3.4.dmg"]);
    fsMock.rmSync.mockImplementation(() => {
      throw new Error("EPERM");
    });

    expect(() => cleanStaleUpdateCache()).not.toThrow();
  });
});

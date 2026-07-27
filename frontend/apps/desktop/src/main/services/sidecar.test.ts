import { beforeEach, describe, expect, it, vi } from "vitest";

const { spawnSyncMock } = vi.hoisted(() => ({ spawnSyncMock: vi.fn() }));

// Full replacement — ``importOriginal`` spread doesn't reliably re-export a Node
// builtin's named functions, and the test only exercises spawnSync. ``spawn`` /
// ``ChildProcess`` are referenced elsewhere in sidecar.ts but not on this path,
// so cheap stand-ins keep the module importable.
vi.mock("node:child_process", () => {
  const mod = {
    spawn: vi.fn(),
    spawnSync: (...args: unknown[]) => spawnSyncMock(...args),
    ChildProcess: class {},
  };
  return { ...mod, default: mod };
});

const { killWindowsProcessTree } = await import("./sidecar");

describe("killWindowsProcessTree", () => {
  beforeEach(() => spawnSyncMock.mockReset());

  it("kills the whole descendant tree with taskkill /T /F (forced, hidden)", () => {
    spawnSyncMock.mockReturnValue({ status: 0 });

    const ok = killWindowsProcessTree(1234);

    expect(ok).toBe(true);
    expect(spawnSyncMock).toHaveBeenCalledWith(
      "taskkill",
      ["/pid", "1234", "/T", "/F"],
      expect.objectContaining({ windowsHide: true }),
    );
  });

  it("reports success even if the process was already gone (taskkill launched)", () => {
    // taskkill returns a non-zero status when the PID is not found, but it DID
    // run — the tree is gone, so no fallback is needed.
    spawnSyncMock.mockReturnValue({ status: 128 });

    expect(killWindowsProcessTree(1234)).toBe(true);
  });

  it("returns false and logs when the taskkill binary itself can't run", () => {
    spawnSyncMock.mockReturnValue({ error: new Error("spawn taskkill ENOENT") });
    const logs: string[] = [];

    const ok = killWindowsProcessTree(1234, (line) => logs.push(line));

    expect(ok).toBe(false);
    expect(logs.join("\n")).toMatch(/taskkill tree-kill failed/);
  });
});

describe("stale-sidecar reclaim helpers", () => {
  beforeEach(() => spawnSyncMock.mockReset());

  const importFresh = async () => await import("./sidecar");

  it("readStaleLockPid parses the pid hint and rejects garbage", async () => {
    const { readStaleLockPid } = await importFresh();
    const fs = await import("node:fs");
    const os = await import("node:os");
    const path = await import("node:path");
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "valuz-lock-"));
    try {
      expect(readStaleLockPid(dir)).toBeNull(); // no lock file
      fs.writeFileSync(path.join(dir, ".single-writer.lock"), "40060");
      expect(readStaleLockPid(dir)).toBe(40060);
      fs.writeFileSync(path.join(dir, ".single-writer.lock"), "garbage");
      expect(readStaleLockPid(dir)).toBeNull();
      fs.writeFileSync(path.join(dir, ".single-writer.lock"), "-5");
      expect(readStaleLockPid(dir)).toBeNull();
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  it("isValuzServerCommand only matches our backend binary", async () => {
    const { isValuzServerCommand } = await importFresh();
    expect(
      isValuzServerCommand(
        "/Applications/Valuz.app/Contents/Resources/libexec/valuz-server --host 127.0.0.1 --port 19100",
      ),
    ).toBe(true);
    expect(isValuzServerCommand('"valuz-server.exe","40060",...')).toBe(true);
    expect(isValuzServerCommand("node next-server")).toBe(false);
    expect(isValuzServerCommand("/usr/bin/python3 -m http.server 19100")).toBe(false);
    expect(isValuzServerCommand(null)).toBe(false);
  });

  it("listPortListeners parses lsof output on POSIX", async () => {
    if (process.platform === "win32") return;
    const { listPortListeners } = await importFresh();
    spawnSyncMock.mockReturnValue({ stdout: "40060\n40123\n\n" });

    expect(listPortListeners(19100)).toEqual([40060, 40123]);
    expect(spawnSyncMock).toHaveBeenCalledWith(
      "lsof",
      ["-ti", "tcp:19100", "-sTCP:LISTEN"],
      expect.objectContaining({ encoding: "utf8" }),
    );
  });

  it("reclaim leaves a non-valuz port squatter alone", async () => {
    if (process.platform === "win32") return;
    const { reclaimStaleSidecar } = await importFresh();
    // lsof → one squatter; ps -o command= → clearly not valuz-server.
    spawnSyncMock.mockImplementation((cmd: string) =>
      cmd === "lsof"
        ? { stdout: "55555\n" }
        : { stdout: "node /some/other/dev-server\n" },
    );
    const killSpy = vi.spyOn(process, "kill").mockImplementation(() => true);
    const logs: string[] = [];
    try {
      await reclaimStaleSidecar("/nonexistent-dir", 19100, (l) => logs.push(l));
      // pidAlive probe (signal 0) is allowed; no SIGTERM/SIGKILL may be sent.
      const lethal = killSpy.mock.calls.filter(([, sig]) => sig !== 0);
      expect(lethal).toEqual([]);
      expect(logs.join("\n")).toContain("leaving it alone");
    } finally {
      killSpy.mockRestore();
    }
  });
});

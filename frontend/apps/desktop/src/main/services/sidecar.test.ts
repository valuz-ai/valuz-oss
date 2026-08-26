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

const {
  configureSidecarEgressEnvironment,
  killWindowsProcessTree,
  resolveSidecarDataDir,
} = await import("./sidecar");

describe("resolveSidecarDataDir", () => {
  it("keeps managed source backends isolated from packaged app data", () => {
    expect(resolveSidecarDataDir(true, { VALUZ_DATA_DIR: "" })).toMatch(
      /\.valuz-oss-dev$/,
    );
    expect(
      resolveSidecarDataDir(true, {
        VALUZ_DATA_DIR: "/tmp/valuz-explicit-dev-data",
      }),
    ).toBe("/tmp/valuz-explicit-dev-data");
  });

  it("keeps packaged sidecars on packaged app data", () => {
    expect(
      resolveSidecarDataDir(false, {
        VALUZ_DATA_DIR: "/tmp/must-not-override-packaged-data",
      }),
    ).toMatch(/\.valuz-oss$/);
  });
});

describe("configureSidecarEgressEnvironment", () => {
  it("scrubs inherited channels and exposes only the stdin marker", () => {
    const env = {
      KEEP: "yes",
      VALUZ_EGRESS_BOOTSTRAP_FILE: "/tmp/stale",
      VALUZ_EGRESS_BOOTSTRAP_STDIN: "stale",
      VALUZ_EGRESS_REQUIRED: "stale",
    };
    configureSidecarEgressEnvironment(
      env,
      {
        mode: "auto",
        controlEndpoint: "http://127.0.0.1:43123",
        bootstrapToken: "memory-only-secret",
        expiresAt: Date.now() + 60_000,
      },
      true,
    );

    expect(env).toEqual({ KEEP: "yes", VALUZ_EGRESS_BOOTSTRAP_STDIN: "1" });
    expect(JSON.stringify(env)).not.toContain("memory-only-secret");
  });

  it("uses only the fail-loud marker when no bootstrap exists", () => {
    const env = {
      VALUZ_EGRESS_BOOTSTRAP_FILE: "/tmp/stale",
      VALUZ_EGRESS_BOOTSTRAP_STDIN: "stale",
    };
    configureSidecarEgressEnvironment(env, null, true);
    expect(env).toEqual({ VALUZ_EGRESS_REQUIRED: "1" });
  });

  it("always exposes the desktop stdin channel without copying its token", () => {
    const env = {
      VALUZ_EGRESS_BOOTSTRAP_STDIN: "stale",
      VALUZ_DESKTOP_BOOTSTRAP_STDIN: "stale",
    };
    configureSidecarEgressEnvironment(
      env,
      null,
      false,
      "desktop-memory-only-token-that-is-long-enough",
    );
    expect(env).toEqual({ VALUZ_DESKTOP_BOOTSTRAP_STDIN: "1" });
    expect(JSON.stringify(env)).not.toContain("desktop-memory-only-token");
  });
});

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

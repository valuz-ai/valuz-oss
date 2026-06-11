import { describe, expect, it, vi } from "vitest";
import {
  detectCliPath,
  detectLoginState,
  getCliStatus,
  launchTerminalWithCommand,
  type CliLoginDeps,
  type ExecResult,
} from "./cli-login";

const HOME = "/home/user";

const ok = (stdout = ""): ExecResult => ({ stdout, stderr: "" });
const fail = (msg = "exit 1") => Promise.reject(new Error(msg));

const makeDeps = (overrides: Partial<CliLoginDeps> = {}): CliLoginDeps => ({
  execFile: vi.fn(async () => ok("")),
  spawnDetached: vi.fn(),
  stat: vi.fn(async () => {
    throw new Error("ENOENT");
  }),
  homedir: () => HOME,
  platform: () => "darwin",
  ...overrides,
});

/**
 * Build deps where `which`/`where` resolves the tool to a fake global path,
 * and the status command against that path returns `statusOut`.
 *
 * `detectLoginState` now resolves the binary first (global → bundled) and
 * only then runs the status command, so the mock has to answer both calls.
 */
const makeGlobalLoginDeps = (
  tool: "claude" | "codex",
  statusOut: ExecResult | (() => Promise<ExecResult>),
  plat = "darwin",
): CliLoginDeps => {
  const globalPath = `/usr/local/bin/${tool}`;
  const statusArgs = tool === "claude" ? ["auth", "status"] : ["login", "status"];
  const whichCmd = plat === "win32" ? "where" : "which";
  return makeDeps({
    platform: () => plat,
    execFile: vi.fn(async (file: string, args: string[]) => {
      if (file === whichCmd) return ok(`${globalPath}\n`);
      if (file === globalPath && JSON.stringify(args) === JSON.stringify(statusArgs)) {
        return typeof statusOut === "function" ? statusOut() : statusOut;
      }
      return ok("");
    }),
  });
};

describe("detectCliPath", () => {
  it("should return resolved path when `which` succeeds", async () => {
    const deps = makeDeps({
      execFile: vi.fn(async () => ok("/opt/homebrew/bin/claude\n")),
    });
    const path = await detectCliPath("claude", deps);
    expect(path).toBe("/opt/homebrew/bin/claude");
  });

  it("should return null when `which` exits non-zero", async () => {
    const deps = makeDeps({ execFile: vi.fn(() => fail()) });
    const path = await detectCliPath("codex", deps);
    expect(path).toBeNull();
  });

  it("should return null when `which` outputs blank", async () => {
    const deps = makeDeps({ execFile: vi.fn(async () => ok("\n")) });
    const path = await detectCliPath("claude", deps);
    expect(path).toBeNull();
  });
});

describe("detectLoginState — codex", () => {
  it("should report logged_in when `codex login status` says 'Logged in using ChatGPT'", async () => {
    const deps = makeGlobalLoginDeps(
      "codex",
      ok("Logged in using ChatGPT (account: jane@acme.io)\n"),
    );
    const state = await detectLoginState("codex", deps);
    expect(state).toBe("logged_in");
  });

  it("should report logged_in when codex is authed with an API key (not a subscription)", async () => {
    const deps = makeGlobalLoginDeps(
      "codex",
      ok("Logged in using an API key - sk-***1234\n"),
    );
    const state = await detectLoginState("codex", deps);
    expect(state).toBe("logged_in");
  });

  it("should report logged_out when the marker is missing from output", async () => {
    const deps = makeGlobalLoginDeps(
      "codex",
      ok("Not logged in. Run `codex login`.\n"),
      "linux",
    );
    const state = await detectLoginState("codex", deps);
    expect(state).toBe("logged_out");
  });

  it("should report logged_out when `codex login status` exits non-zero", async () => {
    const deps = makeGlobalLoginDeps("codex", () => fail("status: not authenticated"), "linux");
    const state = await detectLoginState("codex", deps);
    expect(state).toBe("logged_out");
  });

  it("should accept marker text printed on stderr too", async () => {
    const deps = makeGlobalLoginDeps("codex", {
      stdout: "",
      stderr: "Logged in using ChatGPT\n",
    });
    const state = await detectLoginState("codex", deps);
    expect(state).toBe("logged_in");
  });

  it("should report logged_out on win32 when codex status fails", async () => {
    const deps = makeGlobalLoginDeps("codex", () => fail("not found"), "win32");
    const state = await detectLoginState("codex", deps);
    expect(state).toBe("logged_out");
  });

  it("should not spawn a bare `codex` when no global install exists", async () => {
    // `which` fails (no global codex) → resolveCliBinary falls back to bundled,
    // which doesn't exist in the test env, so detectLoginState returns
    // logged_out WITHOUT ever executing a status command.
    const execFile = vi.fn(() => fail("not on PATH"));
    const deps = makeDeps({ platform: () => "linux", execFile });
    const state = await detectLoginState("codex", deps);
    expect(state).toBe("logged_out");
    // Only the `which` lookup should have run — no bare "codex" status call.
    expect(execFile).toHaveBeenCalledWith("which", ["codex"]);
    expect(execFile).not.toHaveBeenCalledWith("codex", ["login", "status"]);
  });
});

describe("detectLoginState — claude", () => {
  it('should require BOTH `loggedIn: true` and `authMethod: "claude.ai"` markers', async () => {
    const deps = makeGlobalLoginDeps(
      "claude",
      ok('{\n  "loggedIn": true,\n  "authMethod": "claude.ai",\n  "account": "jane"\n}\n'),
    );
    const state = await detectLoginState("claude", deps);
    expect(state).toBe("logged_in");
  });

  it("should report logged_out when `loggedIn` is true but authMethod is not claude.ai", async () => {
    // e.g. an API-key-only login — we only count the OAuth/claude.ai path.
    const deps = makeGlobalLoginDeps(
      "claude",
      ok('{\n  "loggedIn": true,\n  "authMethod": "api_key"\n}'),
    );
    const state = await detectLoginState("claude", deps);
    expect(state).toBe("logged_out");
  });

  it("should report logged_out when `loggedIn` is false even with claude.ai authMethod", async () => {
    const deps = makeGlobalLoginDeps(
      "claude",
      ok('{\n  "loggedIn": false,\n  "authMethod": "claude.ai"\n}'),
    );
    const state = await detectLoginState("claude", deps);
    expect(state).toBe("logged_out");
  });

  it("should report logged_out when `claude auth status` exits non-zero", async () => {
    const deps = makeGlobalLoginDeps("claude", () => fail("not authenticated"), "linux");
    const state = await detectLoginState("claude", deps);
    expect(state).toBe("logged_out");
  });

  it("should accept marker text printed on stderr too", async () => {
    const deps = makeGlobalLoginDeps("claude", {
      stdout: "",
      stderr: '"loggedIn": true,\n"authMethod": "claude.ai",\n',
    }, "linux");
    const state = await detectLoginState("claude", deps);
    expect(state).toBe("logged_in");
  });

  it("should not spawn a bare `claude` when no global install exists", async () => {
    // `which` fails (no global claude) → resolveCliBinary falls back to bundled,
    // which doesn't exist in the test env, so detectLoginState returns
    // logged_out WITHOUT ever executing a status command.
    const execFile = vi.fn(() => fail("not on PATH"));
    const deps = makeDeps({ platform: () => "linux", execFile });
    const state = await detectLoginState("claude", deps);
    expect(state).toBe("logged_out");
    expect(execFile).toHaveBeenCalledWith("which", ["claude"]);
    expect(execFile).not.toHaveBeenCalledWith("claude", ["auth", "status"]);
  });
});

describe("getCliStatus", () => {
  it("should combine cliPath + state for the happy path", async () => {
    const execFile = vi.fn(async (file: string) => {
      if (file === "which") return ok("/usr/local/bin/claude\n");
      if (file === "/usr/local/bin/claude")
        return ok(
          '{\n  "loggedIn": true,\n  "authMethod": "claude.ai",\n  "account": "jane"\n}',
        );
      return ok("");
    });
    const deps = makeDeps({ platform: () => "darwin", execFile });
    const status = await getCliStatus("claude", deps);
    expect(status).toEqual({
      installed: true,
      state: "logged_in",
      cliPath: "/usr/local/bin/claude",
    });
  });

  it("should report installed:false when CLI is missing from PATH", async () => {
    const deps = makeDeps({
      platform: () => "darwin",
      execFile: vi.fn(() => fail()),
    });
    const status = await getCliStatus("codex", deps);
    expect(status.installed).toBe(false);
    expect(status.cliPath).toBeNull();
  });
});

describe("launchTerminalWithCommand — darwin", () => {
  it("should invoke osascript with Terminal.app and the login command", async () => {
    const execFile = vi.fn(async () => ok(""));
    const deps = makeDeps({ platform: () => "darwin", execFile });
    const result = await launchTerminalWithCommand("claude", deps);
    expect(result).toEqual({ launched: true });
    expect(execFile).toHaveBeenCalledWith("osascript", [
      "-e",
      'tell application "Terminal" to activate',
      "-e",
      'tell application "Terminal" to do script "claude /login"',
    ]);
  });

  it("should propagate osascript failure as launched:false", async () => {
    const deps = makeDeps({
      platform: () => "darwin",
      execFile: vi.fn(() => fail("not allowed")),
    });
    const result = await launchTerminalWithCommand("codex", deps);
    expect(result.launched).toBe(false);
    expect(result.error).toBe("not allowed");
  });

  it("should send `codex login` (subcommand) for the codex tool", async () => {
    const execFile = vi.fn(async () => ok(""));
    const deps = makeDeps({ platform: () => "darwin", execFile });
    await launchTerminalWithCommand("codex", deps);
    expect(execFile).toHaveBeenCalledWith("osascript", [
      "-e",
      'tell application "Terminal" to activate',
      "-e",
      'tell application "Terminal" to do script "codex login"',
    ]);
  });
});

describe("launchTerminalWithCommand — linux", () => {
  it("should spawn the first terminal found via `which`", async () => {
    const spawnDetached = vi.fn();
    const execFile = vi.fn(async (_file: string, args: string[]) => {
      const term = args[0];
      if (term === "x-terminal-emulator")
        return ok("/usr/bin/x-terminal-emulator\n");
      return fail();
    });
    const deps = makeDeps({
      platform: () => "linux",
      execFile,
      spawnDetached,
    });
    const result = await launchTerminalWithCommand("claude", deps);
    expect(result).toEqual({ launched: true });
    expect(spawnDetached).toHaveBeenCalledWith("/usr/bin/x-terminal-emulator", [
      "-e",
      "bash",
      "-c",
      "claude /login; exec bash",
    ]);
  });

  it("should run `codex login` inside the spawned terminal", async () => {
    const spawnDetached = vi.fn();
    const execFile = vi.fn(async (_file: string, args: string[]) => {
      const term = args[0];
      if (term === "x-terminal-emulator")
        return ok("/usr/bin/x-terminal-emulator\n");
      return fail();
    });
    const deps = makeDeps({
      platform: () => "linux",
      execFile,
      spawnDetached,
    });
    await launchTerminalWithCommand("codex", deps);
    expect(spawnDetached).toHaveBeenCalledWith("/usr/bin/x-terminal-emulator", [
      "-e",
      "bash",
      "-c",
      "codex login; exec bash",
    ]);
  });

  it("should report no_terminal when none of the known terminals are on PATH", async () => {
    const deps = makeDeps({
      platform: () => "linux",
      execFile: vi.fn(() => fail()),
    });
    const result = await launchTerminalWithCommand("codex", deps);
    expect(result).toEqual({ launched: false, error: "no_terminal" });
  });
});

describe("launchTerminalWithCommand — win32", () => {
  it("should launch via wt.exe when Windows Terminal is available", async () => {
    const spawnDetached = vi.fn();
    const execFile = vi.fn(async (file: string, args: string[]) => {
      if (file === "where" && args[0] === "wt.exe") return ok("C:\\Windows\\wt.exe\n");
      return ok("");
    });
    const deps = makeDeps({
      platform: () => "win32",
      execFile,
      spawnDetached,
    });
    const result = await launchTerminalWithCommand("claude", deps);
    expect(result).toEqual({ launched: true });
    expect(spawnDetached).toHaveBeenCalledWith("wt.exe", [
      "new-tab",
      "--",
      "cmd.exe",
      "/K",
      "claude /login",
    ]);
  });

  it("should fall back to cmd.exe when Windows Terminal is not available", async () => {
    const spawnDetached = vi.fn();
    const execFile = vi.fn(async (file: string, args: string[]) => {
      if (file === "where" && args[0] === "wt.exe") return fail();
      return ok("");
    });
    const deps = makeDeps({
      platform: () => "win32",
      execFile,
      spawnDetached,
    });
    const result = await launchTerminalWithCommand("codex", deps);
    expect(result).toEqual({ launched: true });
    expect(spawnDetached).toHaveBeenCalledWith("cmd.exe", [
      "/K",
      "codex login",
    ]);
  });
});

describe("bundled CLI fallback", () => {
  it("detectCliPath should fall back to bundled claude when which fails", async () => {
    const deps = makeDeps({ execFile: vi.fn(() => fail()) });
    // resolveBundledClaude hits the real filesystem — in the test env neither
    // the production nor the dev path exists, so it returns null.
    const path = await detectCliPath("claude", deps);
    expect(path).toBeNull();
  });

  it("detectCliPath should fall back to bundled codex when which fails", async () => {
    const deps = makeDeps({ execFile: vi.fn(() => fail()) });
    // resolveBundledCodex hits the real filesystem — in the test env neither
    // the production nor the dev path exists, so it returns null.
    const path = await detectCliPath("codex", deps);
    expect(path).toBeNull();
  });
});

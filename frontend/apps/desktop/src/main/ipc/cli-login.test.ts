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
    const execFile = vi.fn(async (file: string, args: string[]) => {
      expect(file).toBe("codex");
      expect(args).toEqual(["login", "status"]);
      return ok("Logged in using ChatGPT (account: jane@acme.io)\n");
    });
    const deps = makeDeps({ platform: () => "darwin", execFile });
    const state = await detectLoginState("codex", deps);
    expect(state).toBe("logged_in");
  });

  it("should report logged_in when codex is authed with an API key (not a subscription)", async () => {
    const deps = makeDeps({
      platform: () => "darwin",
      execFile: vi.fn(async () =>
        ok("Logged in using an API key - sk-***1234\n"),
      ),
    });
    const state = await detectLoginState("codex", deps);
    expect(state).toBe("logged_in");
  });

  it("should report logged_out when the marker is missing from output", async () => {
    const deps = makeDeps({
      platform: () => "linux",
      execFile: vi.fn(async () => ok("Not logged in. Run `codex login`.\n")),
    });
    const state = await detectLoginState("codex", deps);
    expect(state).toBe("logged_out");
  });

  it("should report logged_out when `codex login status` exits non-zero", async () => {
    const deps = makeDeps({
      platform: () => "linux",
      execFile: vi.fn(() => fail("status: not authenticated")),
    });
    const state = await detectLoginState("codex", deps);
    expect(state).toBe("logged_out");
  });

  it("should accept marker text printed on stderr too", async () => {
    const deps = makeDeps({
      platform: () => "darwin",
      execFile: vi.fn(async () => ({
        stdout: "",
        stderr: "Logged in using ChatGPT\n",
      })),
    });
    const state = await detectLoginState("codex", deps);
    expect(state).toBe("logged_in");
  });

  it("should report logged_out on win32 when codex status fails", async () => {
    const deps = makeDeps({
      platform: () => "win32",
      execFile: vi.fn(() => fail("not found")),
    });
    const state = await detectLoginState("codex", deps);
    expect(state).toBe("logged_out");
  });
});

describe("detectLoginState — claude", () => {
  it('should require BOTH `loggedIn: true` and `authMethod: "claude.ai"` markers', async () => {
    const execFile = vi.fn(async (file: string, args: string[]) => {
      expect(file).toBe("claude");
      expect(args).toEqual(["auth", "status"]);
      return ok(
        '{\n  "loggedIn": true,\n  "authMethod": "claude.ai",\n  "account": "jane"\n}\n',
      );
    });
    const deps = makeDeps({ platform: () => "darwin", execFile });
    const state = await detectLoginState("claude", deps);
    expect(state).toBe("logged_in");
  });

  it("should report logged_out when `loggedIn` is true but authMethod is not claude.ai", async () => {
    // e.g. an API-key-only login — we only count the OAuth/claude.ai path.
    const deps = makeDeps({
      platform: () => "darwin",
      execFile: vi.fn(async () =>
        ok('{\n  "loggedIn": true,\n  "authMethod": "api_key"\n}'),
      ),
    });
    const state = await detectLoginState("claude", deps);
    expect(state).toBe("logged_out");
  });

  it("should report logged_out when `loggedIn` is false even with claude.ai authMethod", async () => {
    const deps = makeDeps({
      platform: () => "darwin",
      execFile: vi.fn(async () =>
        ok('{\n  "loggedIn": false,\n  "authMethod": "claude.ai"\n}'),
      ),
    });
    const state = await detectLoginState("claude", deps);
    expect(state).toBe("logged_out");
  });

  it("should report logged_out when `claude auth status` exits non-zero", async () => {
    const deps = makeDeps({
      platform: () => "linux",
      execFile: vi.fn(() => fail("not authenticated")),
    });
    const state = await detectLoginState("claude", deps);
    expect(state).toBe("logged_out");
  });

  it("should accept marker text printed on stderr too", async () => {
    const deps = makeDeps({
      platform: () => "linux",
      execFile: vi.fn(async () => ({
        stdout: "",
        stderr: '"loggedIn": true,\n"authMethod": "claude.ai",\n',
      })),
    });
    const state = await detectLoginState("claude", deps);
    expect(state).toBe("logged_in");
  });
});

describe("getCliStatus", () => {
  it("should combine cliPath + state for the happy path", async () => {
    const execFile = vi.fn(async (file: string) => {
      if (file === "which") return ok("/usr/local/bin/claude\n");
      if (file === "claude")
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

  it("detectLoginState should try bundled claude when global claude auth fails", async () => {
    const execFile = vi.fn(async (file: string) => {
      // First call: global "claude auth status" fails
      if (file === "claude") throw new Error("not found");
      // resolveBundledClaude returns null in test env, so no second call
      return ok("");
    });
    const deps = makeDeps({ platform: () => "darwin", execFile });
    const state = await detectLoginState("claude", deps);
    expect(state).toBe("logged_out");
  });

  it("detectCliPath should fall back to bundled codex when which fails", async () => {
    const deps = makeDeps({ execFile: vi.fn(() => fail()) });
    // resolveBundledCodex hits the real filesystem — in the test env neither
    // the production nor the dev path exists, so it returns null.
    const path = await detectCliPath("codex", deps);
    expect(path).toBeNull();
  });

  it("detectLoginState should try bundled codex when global codex status fails", async () => {
    const execFile = vi.fn(async (file: string) => {
      // First call: global "codex login status" fails
      if (file === "codex") throw new Error("not found");
      // resolveBundledCodex returns null in test env, so no second call
      return ok("");
    });
    const deps = makeDeps({ platform: () => "darwin", execFile });
    const state = await detectLoginState("codex", deps);
    expect(state).toBe("logged_out");
  });
});

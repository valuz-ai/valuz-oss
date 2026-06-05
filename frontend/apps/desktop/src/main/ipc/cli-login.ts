import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import { existsSync, readdirSync } from "node:fs";
import { stat } from "node:fs/promises";
import { homedir, platform } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const execFileAsync = promisify(execFile);

// ESM __dirname equivalent for dev-mode path resolution.
const __filename = fileURLToPath(import.meta.url);
const __dirname = join(__filename, "..");

export type CliTool = "claude" | "codex";
export type LoginState = "logged_in" | "logged_out" | "unsupported";

export interface CliStatus {
  installed: boolean;
  state: LoginState;
  cliPath: string | null;
}

export interface LaunchResult {
  launched: boolean;
  error?: string;
}

export interface ExecResult {
  stdout: string;
  stderr: string;
}

export interface StatLike {
  size: number;
  mode: number;
}

export interface CliLoginDeps {
  execFile: (file: string, args: string[]) => Promise<ExecResult>;
  spawnDetached: (file: string, args: string[]) => void;
  stat: (path: string) => Promise<StatLike>;
  homedir: () => string;
  platform: () => string;
}

export const defaultDeps: CliLoginDeps = {
  execFile: async (file, args) => {
    const { stdout, stderr } = await execFileAsync(file, args);
    return { stdout: String(stdout), stderr: String(stderr) };
  },
  spawnDetached: (file, args) => {
    const child = spawn(file, args, { detached: true, stdio: "ignore" });
    child.unref();
  },
  stat: async (path) => {
    const s = await stat(path);
    return { size: s.size, mode: s.mode };
  },
  homedir,
  platform,
};

// Strict markers we require in each CLI's status output before declaring a

/**
 * Resolve the bundled Claude CLI shipped inside the backend's PyInstaller bundle.
 *
 * Production layout (same as sidecar.ts):
 *   Valuz.app/Contents/Resources/libexec/
 *     ├── valuz-server
 *     └── _internal/claude_agent_sdk/_bundled/claude
 *
 * Dev layout (the python3.X minor version isn't fixed — it tracks whatever
 * interpreter `uv` resolved for the venv, so we glob it):
 *   backend/.venv/lib/python3.X/site-packages/claude_agent_sdk/_bundled/claude
 */
function resolveBundledClaude(): string | null {
  const binaryName = platform() === "win32" ? "claude.exe" : "claude";

  // Production: packaged Electron app. ``process.resourcesPath`` is an
  // Electron-only global — undefined under plain Node (e.g. vitest), where
  // ``join(undefined, …)`` would throw. Guard it so the function stays
  // callable off-Electron and the dev fallback below remains reachable.
  if (process.resourcesPath) {
    const bundled = join(
      process.resourcesPath,
      "libexec",
      "_internal",
      "claude_agent_sdk",
      "_bundled",
      binaryName,
    );
    if (existsSync(bundled)) return bundled;
  }

  // Dev: walk up from dist-electron/ to the repo root, then into the backend
  // venv. dist-electron sits at frontend/apps/desktop/dist-electron, so the
  // repo root is exactly 4 levels up (desktop → apps → frontend → repo) — an
  // earlier 5th ".." overshot to the repo's parent and the fallback never
  // resolved. The Python minor version in the path (lib/python3.X/) isn't
  // pinned — it follows whatever interpreter `uv` resolved — so glob every
  // python3.* dir instead of hardcoding one (hardcoding 3.13 silently missed
  // a 3.12 venv, making the bundled claude read as "not installed").
  const venvLib = resolve(
    __dirname,
    "..",
    "..",
    "..",
    "..",
    "backend",
    ".venv",
    "lib",
  );
  let pyDirs: string[] = [];
  try {
    pyDirs = readdirSync(venvLib)
      .filter((d) => d.startsWith("python3."))
      .sort()
      .reverse(); // prefer the newest minor when several venvs coexist
  } catch {
    // venv lib dir absent (no dev venv) — nothing to glob
  }
  for (const py of pyDirs) {
    const devClaude = join(
      venvLib,
      py,
      "site-packages",
      "claude_agent_sdk",
      "_bundled",
      binaryName,
    );
    if (existsSync(devClaude)) return devClaude;
  }

  return null;
}

// Strict markers we require in each CLI's status output before declaring a
// successful login. The previous heuristics (keychain entry exists / file
// non-empty) returned true for stale or partially provisioned credentials
// — only the CLIs themselves know whether the auth is actually usable, so
// we shell out and parse their authoritative answer.
//
// claude — ``claude auth status`` JSON-ish: must contain BOTH markers.
// codex  — ``codex login status`` plaintext: accept ANY login method, not
//          just the ChatGPT subscription. The CLI prints one of
//          "Logged in using ChatGPT" / "Logged in using an API key - …" /
//          "Logged in using access token" when authed, and "Not logged in"
//          otherwise — so the shared "Logged in using" prefix is the marker
//          (it can't match "Not logged in"). Keying only on ChatGPT made an
//          API-key codex read as logged_out even though the codex runtime
//          runs fine off that key.
const CLAUDE_STATUS_MARKERS = [
  '"loggedIn": true,',
  '"authMethod": "claude.ai",',
];
const CODEX_STATUS_MARKER = "Logged in using";

const LINUX_TERMINALS = [
  "x-terminal-emulator",
  "gnome-terminal",
  "konsole",
  "xfce4-terminal",
  "xterm",
] as const;

// Some CLIs print the status on stderr (claude does, codex split). Search
// both streams so the strict-marker check works regardless.
const allOutput = (r: ExecResult): string => `${r.stdout}\n${r.stderr}`;

export const detectCliPath = async (
  tool: CliTool,
  deps: CliLoginDeps = defaultDeps,
): Promise<string | null> => {
  try {
    const { stdout } = await deps.execFile("which", [tool]);
    const path = stdout.trim();
    if (path.length > 0) return path;
  } catch {
    // which failed — fall through
  }

  // Fallback: use the bundled claude from the backend's PyInstaller bundle
  // when no global install is found.
  if (tool === "claude") {
    return resolveBundledClaude();
  }

  return null;
};

export const detectLoginState = async (
  tool: CliTool,
  deps: CliLoginDeps = defaultDeps,
): Promise<LoginState> => {
  const plat = deps.platform();

  if (plat !== "darwin" && plat !== "linux") {
    return "unsupported";
  }

  // Authoritative path for both CLIs: shell out to the tool's own status
  // command and require strict marker text in the output. The CLI is the
  // only thing that knows whether the auth artefact on disk / in keychain
  // is actually usable — file presence + file mode were too permissive
  // (stale tokens, half-provisioned files, wrong account types all read
  // as "logged_in" before).
  if (tool === "claude") {
    try {
      const result = await deps.execFile("claude", ["auth", "status"]);
      const out = allOutput(result);
      const allPresent = CLAUDE_STATUS_MARKERS.every((m) => out.includes(m));
      return allPresent ? "logged_in" : "logged_out";
    } catch {
      // Global claude failed — try the bundled claude from the backend
      const bundled = resolveBundledClaude();
      if (bundled) {
        try {
          const result = await deps.execFile(bundled, ["auth", "status"]);
          const out = allOutput(result);
          const allPresent = CLAUDE_STATUS_MARKERS.every((m) =>
            out.includes(m),
          );
          return allPresent ? "logged_in" : "logged_out";
        } catch {
          return "logged_out";
        }
      }
      return "logged_out";
    }
  }

  // tool === 'codex'
  try {
    const result = await deps.execFile("codex", ["login", "status"]);
    const out = allOutput(result);
    return out.includes(CODEX_STATUS_MARKER) ? "logged_in" : "logged_out";
  } catch {
    return "logged_out";
  }
};

export const getCliStatus = async (
  tool: CliTool,
  deps: CliLoginDeps = defaultDeps,
): Promise<CliStatus> => {
  const cliPath = await detectCliPath(tool, deps);
  const state = await detectLoginState(tool, deps);
  return { installed: cliPath !== null, state, cliPath };
};

const findLinuxTerminal = async (
  deps: CliLoginDeps,
): Promise<string | null> => {
  for (const term of LINUX_TERMINALS) {
    try {
      const { stdout } = await deps.execFile("which", [term]);
      const path = stdout.trim();
      if (path.length > 0) return path;
    } catch {
      // try next
    }
  }
  return null;
};

// Each CLI surfaces its login flow through a different invocation:
//   - claude uses an in-REPL slash command (``claude /login``)
//   - codex uses a regular subcommand (``codex login``)
// Treat them as data, not as ``${tool} /login`` formatting, so adding new
// tools later doesn't have to keep matching the wrong shape.
const LOGIN_COMMAND: Record<CliTool, string> = {
  claude: "claude /login",
  codex: "codex login",
};

export const launchTerminalWithCommand = async (
  tool: CliTool,
  deps: CliLoginDeps = defaultDeps,
): Promise<LaunchResult> => {
  const plat = deps.platform();
  const command = LOGIN_COMMAND[tool];

  if (plat === "darwin") {
    try {
      await deps.execFile("osascript", [
        "-e",
        'tell application "Terminal" to activate',
        "-e",
        `tell application "Terminal" to do script "${command}"`,
      ]);
      return { launched: true };
    } catch (err) {
      return {
        launched: false,
        error: err instanceof Error ? err.message : "osascript failed",
      };
    }
  }

  if (plat === "linux") {
    const term = await findLinuxTerminal(deps);
    if (!term) {
      return { launched: false, error: "no_terminal" };
    }
    try {
      deps.spawnDetached(term, ["-e", "bash", "-c", `${command}; exec bash`]);
      return { launched: true };
    } catch (err) {
      return {
        launched: false,
        error: err instanceof Error ? err.message : "spawn failed",
      };
    }
  }

  return { launched: false, error: "unsupported_platform" };
};

import { ChildProcess, spawn } from "node:child_process";
import fs from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Vite bundles the Electron main process as ESM (vite.main.config.ts
// formats:['es']), so the CommonJS ``__dirname`` global is undefined at
// runtime. Recreate it from import.meta.url for the dev-mode lookups
// below. Production paths use ``process.resourcesPath`` and never hit this.
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export interface SidecarOptions {
  appDataDir: string;
  name: string;
  port: number;
  onLog?: (line: string) => void;
  onExit?: (code: number | null, signal: string | null) => void;
}

export interface DesktopSidecarResult {
  name: string;
  pid: number | null;
  port: number;
  stop: () => void;
}

/**
 * Locate the backend server binary.
 *
 * Layout: PyInstaller's onedir contents (binary + _internal/) are staged
 * directly under libexec/, with no extra ``valuz-server/`` wrapper dir:
 *   Valuz.app/Contents/Resources/libexec/
 *     ├── valuz-server     (executable)
 *     ├── _internal/       (PyInstaller runtime, sibling of the binary)
 *     └── rg               (helper)
 *
 * Lookup order:
 * 1. Production: ``process.resourcesPath/libexec/valuz-server``
 * 2. Dev (built binary): project ``resources/libexec/valuz-server``
 * 3. Returns null when no bundled binary is found (caller falls back to ``uv run``).
 */
function resolveServerBinary(): string | null {
  // Production: packaged Electron app
  const bundled = path.join(process.resourcesPath, "libexec", "valuz-server");
  if (fs.existsSync(bundled)) return bundled;

  // Dev: check if the build script has placed a binary in the project tree.
  // From dist-electron/ we walk up to desktop/, then into resources/.
  const devBinary = path.join(
    __dirname,
    "..",
    "..",
    "resources",
    "libexec",
    "valuz-server",
  );
  if (fs.existsSync(devBinary)) return devBinary;

  return null;
}

/**
 * Locate the bundled ripgrep helper.
 *
 * Layout per docs/STRUCTURE.md §"Desktop Distribution":
 *   Valuz.app/Contents/Resources/libexec/rg
 *
 * Returns null when no bundled rg is found; the backend's
 * docs_embedded._detect_rg() then falls back to shutil.which("rg").
 */
function resolveRgBinary(): string | null {
  const bundledExt = process.platform === "win32" ? "rg.exe" : "rg";

  const bundled = path.join(process.resourcesPath, "libexec", bundledExt);
  if (fs.existsSync(bundled)) return bundled;

  const devRg = path.join(
    __dirname,
    "..",
    "..",
    "resources",
    "libexec",
    bundledExt,
  );
  if (fs.existsSync(devRg)) return devRg;

  return null;
}

/**
 * Build spawn arguments for dev-mode fallback (uv run python -m valuz_agent).
 */
function buildDevSpawnArgs(port: number): {
  cmd: string;
  args: string[];
  cwd: string;
} {
  const backendDir = path.resolve(
    __dirname,
    "..",
    "..",
    "..",
    "..",
    "..",
    "backend",
  );
  return {
    cmd: "uv",
    args: [
      "run",
      "python",
      "-m",
      "valuz_agent",
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
    ],
    cwd: backendDir,
  };
}

/**
 * Start the backend server as a managed sidecar process.
 *
 * Spawns the bundled PyInstaller binary if available, otherwise falls back
 * to launching via `uv run` for development.
 */
export const startSidecar = async (
  options: SidecarOptions,
): Promise<DesktopSidecarResult> => {
  // ``appDataDir`` is kept in SidecarOptions for callers, but the env
  // override below uses a literal path; destructure only what we read.
  const { name, port, onLog, onExit } = options;

  const serverBinary = resolveServerBinary();

  let cmd: string;
  let args: string[];
  let cwd: string | undefined;
  // ``VALUZ_DATA_DIR`` MUST be an absolute path. A literal ``"~/..."``
  // gets passed verbatim to pydantic-settings, which does not expand
  // ``~`` — the backend then tries to mkdir under cwd, fails on root
  // (Electron's default cwd) with "Read-only file system: '~'", and
  // the PyInstaller bootloader prints "Failed to execute script
  // _pyinstaller_entry" before the sidecar can even log a line.
  const env: Record<string, string> = {
    ...(process.env as Record<string, string>),
    VALUZ_DATA_DIR: path.join(homedir(), ".valuz", "app"),
  };

  // Point the backend's docs_embedded._detect_rg() at the bundled binary.
  // It already honours VALUZ_RG_PATH ahead of bundled / PATH lookup.
  const rgBinary = resolveRgBinary();
  if (rgBinary) {
    env.VALUZ_RG_PATH = rgBinary;
  }

  if (serverBinary) {
    cmd = serverBinary;
    args = ["--host", "127.0.0.1", "--port", String(port)];
  } else {
    const dev = buildDevSpawnArgs(port);
    cmd = dev.cmd;
    args = dev.args;
    cwd = dev.cwd;
  }

  const child: ChildProcess = spawn(cmd, args, {
    cwd,
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });

  // Capture stdout/stderr for logs
  const captureStream = (
    stream: NodeJS.ReadableStream | null,
    prefix: string,
  ) => {
    if (!stream) return;
    stream.setEncoding("utf8");
    stream.on("data", (chunk: string) => {
      for (const line of chunk.split("\n")) {
        const trimmed = line.trim();
        if (trimmed) {
          onLog?.(`${prefix}${trimmed}`);
        }
      }
    });
  };

  captureStream(child.stdout, "");
  captureStream(child.stderr, "[stderr] ");

  // Track lifecycle
  let stopped = false;

  child.on("exit", (code, signal) => {
    if (!stopped) {
      onExit?.(code, signal);
    }
  });

  child.on("error", (err) => {
    onLog?.(`[error] Failed to start sidecar: ${err.message}`);
  });

  const stop = () => {
    if (stopped || !child.pid) return;
    stopped = true;

    // Send SIGTERM for graceful shutdown
    try {
      child.kill("SIGTERM");
    } catch {
      // Process may have already exited
    }

    // Force kill after 5 seconds
    const timeout = setTimeout(() => {
      try {
        child.kill("SIGKILL");
      } catch {
        // Already dead
      }
    }, 5000);

    // Clean up timeout if process exits on its own
    child.on("exit", () => clearTimeout(timeout));
  };

  return {
    name,
    pid: child.pid ?? null,
    port,
    stop,
  };
};

/**
 * @deprecated Use startSidecar instead.
 */
export const startSidecarSkeleton = startSidecar;

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { lstat, readlink, mkdir } from "node:fs/promises";
import { join, dirname } from "node:path";
import { app } from "electron";

const execFileAsync = promisify(execFile);

export interface CliInstallStatus {
  installed: boolean;
  linkPath: string;
  targetPath: string;
}

export interface CliInstallResult {
  success: boolean;
  error?: string;
}

// Resolve the bundled valuz CLI binary path.
// In production: Resources/bin/valuz (macOS .app bundle layout).
// In dev: falls back to cli/build/valuz (Go build output).
function resolveBundledCliPath(): string {
  if (app.isPackaged) {
    return join(process.resourcesPath, "bin", "valuz");
  }
  // Dev fallback — resolve relative to the desktop app root.
  return join(app.getAppPath(), "..", "..", "cli", "build", "valuz");
}

// Preferred symlink location on macOS/Linux.
function defaultLinkPath(): string {
  return "/usr/local/bin/valuz";
}

// Check whether the CLI is already linked into PATH.
// Returns the current install status.
export async function getCliInstallStatus(): Promise<CliInstallStatus> {
  const linkPath = defaultLinkPath();
  const targetPath = resolveBundledCliPath();

  try {
    const st = await lstat(linkPath);
    if (st.isSymbolicLink()) {
      const resolved = await readlink(linkPath);
      // Symlink already points to the right target (or the bundle is
      // at the same path — accept exact match).
      if (resolved === targetPath) {
        return { installed: true, linkPath, targetPath };
      }
    }
  } catch {
    // File doesn't exist — not installed.
  }
  return { installed: false, linkPath, targetPath };
}

// Create a symlink at linkPath → targetPath using macOS admin
// elevation via AppleScript. This is the same approach VS Code uses
// for "Shell Command: Install 'code' command in PATH".
export async function installCliToPath(): Promise<CliInstallResult> {
  const linkPath = defaultLinkPath();
  const targetPath = resolveBundledCliPath();

  // Quick check: already installed?
  const status = await getCliInstallStatus();
  if (status.installed) {
    return { success: true };
  }

  // Ensure the target binary actually exists.
  try {
    await lstat(targetPath);
  } catch {
    return {
      success: false,
      error: `CLI binary not found at ${targetPath}`,
    };
  }

  // Ensure the parent directory exists.
  const parentDir = dirname(linkPath);
  try {
    await mkdir(parentDir, { recursive: true });
  } catch {
    // May fail without elevation — the osascript below will handle it.
  }

  if (process.platform === "darwin") {
    return installMacos(linkPath, targetPath);
  }

  if (process.platform === "linux") {
    return installLinux(linkPath, targetPath);
  }

  return { success: false, error: "unsupported_platform" };
}

async function installMacos(
  linkPath: string,
  targetPath: string,
): Promise<CliInstallResult> {
  const cmd = `ln -sf "${targetPath}" "${linkPath}"`;
  try {
    // Use AppleScript to run the symlink command with admin privileges.
    // This shows the system authentication dialog.
    await execFileAsync("osascript", [
      "-e",
      `do shell script "${cmd}" with administrator privileges`,
    ]);
    return { success: true };
  } catch (err) {
    // User cancelled the auth dialog, or the command failed.
    const msg = err instanceof Error ? err.message : "Failed to create symlink";
    if (msg.includes("User canceled") || msg.includes("-128")) {
      return { success: false, error: "cancelled" };
    }
    return { success: false, error: msg };
  }
}

async function installLinux(
  linkPath: string,
  targetPath: string,
): Promise<CliInstallResult> {
  // On Linux, try pkexec for GUI elevation, fall back to direct ln.
  try {
    await execFileAsync("pkexec", ["ln", "-sf", targetPath, linkPath]);
    return { success: true };
  } catch {
    // pkexec not available or cancelled — try direct (works if user
    // has write access to /usr/local/bin).
    try {
      await execFileAsync("ln", ["-sf", targetPath, linkPath]);
      return { success: true };
    } catch (err) {
      return {
        success: false,
        error: err instanceof Error ? err.message : "Failed to create symlink",
      };
    }
  }
}

// Remove the symlink from PATH.
export async function uninstallCliFromPath(): Promise<CliInstallResult> {
  const linkPath = defaultLinkPath();

  // Check it exists.
  try {
    await lstat(linkPath);
  } catch {
    return { success: true }; // Already gone.
  }

  if (process.platform === "darwin") {
    return uninstallMacos(linkPath);
  }

  if (process.platform === "linux") {
    return uninstallLinux(linkPath);
  }

  return { success: false, error: "unsupported_platform" };
}

async function uninstallMacos(linkPath: string): Promise<CliInstallResult> {
  const cmd = `rm -f "${linkPath}"`;
  try {
    await execFileAsync("osascript", [
      "-e",
      `do shell script "${cmd}" with administrator privileges`,
    ]);
    return { success: true };
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Failed to remove symlink";
    if (msg.includes("User canceled") || msg.includes("-128")) {
      return { success: false, error: "cancelled" };
    }
    return { success: false, error: msg };
  }
}

async function uninstallLinux(linkPath: string): Promise<CliInstallResult> {
  try {
    await execFileAsync("pkexec", ["rm", "-f", linkPath]);
    return { success: true };
  } catch {
    try {
      await execFileAsync("rm", ["-f", linkPath]);
      return { success: true };
    } catch (err) {
      return {
        success: false,
        error: err instanceof Error ? err.message : "Failed to remove symlink",
      };
    }
  }
}

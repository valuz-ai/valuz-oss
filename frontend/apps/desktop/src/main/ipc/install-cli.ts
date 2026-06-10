import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { lstat, readlink, mkdir, copyFile, unlink } from "node:fs/promises";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
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
  const binaryName = process.platform === "win32" ? "valuz.exe" : "valuz";
  if (app.isPackaged) {
    return join(process.resourcesPath, "bin", binaryName);
  }
  // Dev fallback — resolve relative to the desktop app root.
  return join(app.getAppPath(), "..", "..", "cli", "build", binaryName);
}

// Preferred symlink/copy location per platform.
// macOS/Linux: /usr/local/bin/valuz (symlink)
// Windows: %APPDATA%\Valuz\valuz.exe (copy — symlinks need Developer Mode)
function defaultLinkPath(): string {
  if (process.platform === "win32") {
    return join(
      process.env.APPDATA || join(homedir(), "AppData", "Roaming"),
      "Valuz",
      "valuz.exe",
    );
  }
  return "/usr/local/bin/valuz";
}

// Check whether the CLI is already linked/copied into PATH.
// Returns the current install status.
export async function getCliInstallStatus(): Promise<CliInstallStatus> {
  const linkPath = defaultLinkPath();
  const targetPath = resolveBundledCliPath();

  if (process.platform === "win32") {
    // Windows: check for file existence (not a symlink)
    if (existsSync(linkPath)) {
      return { installed: true, linkPath, targetPath };
    }
    return { installed: false, linkPath, targetPath };
  }

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

  if (process.platform === "win32") {
    return installWindows(linkPath, targetPath);
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

  if (process.platform === "win32") {
    return uninstallWindows(linkPath);
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

async function installWindows(
  linkPath: string,
  targetPath: string,
): Promise<CliInstallResult> {
  const destDir = dirname(linkPath);
  try {
    await mkdir(destDir, { recursive: true });
  } catch {
    // Will fail below if dir can't be created
  }

  try {
    await copyFile(targetPath, linkPath);
  } catch (err) {
    return {
      success: false,
      error: err instanceof Error ? err.message : "Failed to copy CLI binary",
    };
  }

  // Add destDir to user PATH via PowerShell.
  try {
    const psScript = `[Environment]::SetEnvironmentVariable('Path', [Environment]::GetEnvironmentVariable('Path', 'User') + ';${destDir}', 'User')`;
    await execFileAsync("powershell", ["-NoProfile", "-Command", psScript]);
  } catch {
    // PATH update failed — binary is still copied, user can add to PATH manually.
    return {
      success: false,
      error: `CLI copied to ${linkPath} but failed to update PATH automatically. Add ${destDir} to your PATH manually.`,
    };
  }

  return { success: true };
}

async function uninstallWindows(
  linkPath: string,
): Promise<CliInstallResult> {
  try {
    await unlink(linkPath);
  } catch (err) {
    return {
      success: false,
      error: err instanceof Error ? err.message : "Failed to remove CLI binary",
    };
  }

  // Remove the directory from user PATH via PowerShell.
  const destDir = dirname(linkPath);
  try {
    const psScript = `$p = [Environment]::GetEnvironmentVariable('Path', 'User'); $p = ($p -split ';' | Where-Object { $_ -ne '${destDir}' }) -join ';'; [Environment]::SetEnvironmentVariable('Path', $p, 'User')`;
    await execFileAsync("powershell", ["-NoProfile", "-Command", psScript]);
  } catch {
    // PATH cleanup failed — binary is still removed.
  }

  return { success: true };
}

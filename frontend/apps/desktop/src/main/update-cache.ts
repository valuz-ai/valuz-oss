import { existsSync, readdirSync, readFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { app } from 'electron'

/**
 * electron-updater's differential-download state, keyed by the fixed names it
 * writes after a successful download:
 * ``MacUpdater`` → ``update.zip``; ``NsisUpdater`` →
 * ``installer.exe`` / ``package.7z`` (``CURRENT_APP_INSTALLER_FILE_NAME`` /
 * ``CURRENT_APP_PACKAGE_FILE_NAME`` in builder-util-runtime).
 *
 * One of these is the whole input to the next release's diff, so keeping ~600 MB
 * on disk buys back a ~600 MB download every single update.
 */
const DIFFERENTIAL_STATE_FILES = new Set(['update.zip', 'installer.exe', 'package.7z'])

/**
 * Delete the downloaded update artifacts a prior version left in
 * electron-updater's cache (the versioned ``.zip`` / ``.blockmap`` / ``pending``
 * stage + the ``update-info.json`` state file). Each package is ~600 MB, so a
 * stale one — after an install, or a crashed/aborted download — just wastes
 * disk and can confuse a later update.
 *
 * Everything except electron-updater's own differential-download state, which
 * must survive: after a successful download it keeps the package under a fixed
 * name (``update.zip`` on macOS, ``installer.exe`` / ``package.7z`` for NSIS)
 * and diffs the *next* release against it. Deleting those turns every update
 * into a full download — which is exactly what this function used to do, since
 * ``update.zip`` matches ``\.zip$``.
 *
 * Call this ONLY after the app has confirmed a healthy start (backend up),
 * never during boot: it keeps the previous version's package around as a
 * fallback until the new build proves it actually runs, and avoids doing ~GB of
 * disk I/O on the startup path. The current session's download (if any) happens
 * later and is untouched; electron-updater recreates the cache on the next one.
 *
 * Deliberately free of the ``electron-updater`` import (whose ``autoUpdater``
 * getter constructs on load and needs a real ``app``), so it stays importable
 * from the service layer + unit tests.
 */
export const cleanStaleUpdateCache = () => {
  if (!app?.isPackaged) {
    return
  }
  try {
    // ``updaterCacheDirName`` is written by electron-builder into app-update.yml;
    // read it so we don't hard-code the (config-driven) cache folder name.
    const cfgPath = join(process.resourcesPath, 'app-update.yml')
    if (!existsSync(cfgPath)) {
      return
    }
    const match = readFileSync(cfgPath, 'utf8').match(
      /updaterCacheDirName:\s*['"]?([^'"\n\r]+)['"]?/,
    )
    if (!match) {
      return
    }
    // electron-updater's cache base (env-paths' "cache"): ~/Library/Caches on
    // macOS, %LOCALAPPDATA% on Windows, $XDG_CACHE_HOME|~/.cache on Linux.
    // (``app.getPath`` has no "cache" entry, so compute it per-platform.)
    const home = app.getPath('home')
    const cacheBase =
      process.platform === 'darwin'
        ? join(home, 'Library', 'Caches')
        : process.platform === 'win32'
          ? process.env.LOCALAPPDATA || join(home, 'AppData', 'Local')
          : process.env.XDG_CACHE_HOME || join(home, '.cache')
    const cacheDir = join(cacheBase, match[1].trim())
    if (!existsSync(cacheDir)) {
      return
    }
    let removed = 0
    for (const name of readdirSync(cacheDir)) {
      if (DIFFERENTIAL_STATE_FILES.has(name.toLowerCase())) {
        continue
      }
      if (
        name === 'pending' ||
        name === 'update-info.json' ||
        /\.(zip|blockmap|7z|dmg|nupkg|exe|AppImage)$/i.test(name)
      ) {
        rmSync(join(cacheDir, name), { recursive: true, force: true })
        removed += 1
      }
    }
    if (removed > 0) {
      console.info(
        `[update-cache] cleaned ${removed} stale update artifact(s) in ${cacheDir}`,
      )
    }
  } catch (err) {
    // Best-effort disk hygiene — never let it affect the app.
    console.warn(`[update-cache] failed to clean stale update cache: ${String(err)}`)
  }
}

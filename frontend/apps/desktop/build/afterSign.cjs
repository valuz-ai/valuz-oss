const { execFileSync, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { notarize } = require('@electron/notarize');

const MACHO_MAGICS = new Set([
  0xfeedface, 0xfeedfacf, 0xcefaedfe, 0xcffaedfe, 0xcafebabe, 0xbebafeca,
]);

function isMachO(filePath) {
  let fd;
  try {
    fd = fs.openSync(filePath, 'r');
    const buf = Buffer.alloc(4);
    const n = fs.readSync(fd, buf, 0, 4, 0);
    if (n < 4) return false;
    return MACHO_MAGICS.has(buf.readUInt32BE(0)) || MACHO_MAGICS.has(buf.readUInt32LE(0));
  } catch {
    return false;
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

function findMachOFiles(dir, out = []) {
  if (!fs.existsSync(dir)) return out;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isSymbolicLink()) continue;
    if (entry.isDirectory()) findMachOFiles(p, out);
    else if (entry.isFile() && isMachO(p)) out.push(p);
  }
  return out;
}

function findFrameworks(dir, out = []) {
  if (!fs.existsSync(dir)) return out;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isSymbolicLink()) continue;
    if (entry.isDirectory()) {
      if (entry.name.endsWith('.framework')) out.push(p);
      else findFrameworks(p, out);
    }
  }
  return out;
}

function rmAny(p) {
  const stat = fs.lstatSync(p);
  if (stat.isDirectory() && !stat.isSymbolicLink()) fs.rmSync(p, { recursive: true, force: true });
  else fs.unlinkSync(p);
}

function ensureSymlink(target, linkPath) {
  if (fs.existsSync(linkPath) || fs.lstatSync(linkPath, { throwIfNoEntry: false })) {
    const stat = fs.lstatSync(linkPath);
    if (stat.isSymbolicLink()) {
      const cur = fs.readlinkSync(linkPath);
      if (cur === target) return;
      fs.unlinkSync(linkPath);
    } else {
      rmAny(linkPath);
    }
  }
  fs.symlinkSync(target, linkPath);
}

// PyInstaller's Python.framework uses a flat layout (binary + Resources/ at framework root,
// Versions/Current as a real directory). Apple's codesign rejects this with
// "bundle format is ambiguous". Convert it to the canonical Apple framework layout
// before signing — the canonical real binary lives at Versions/<X.Y>/<name>.
function normalizeFramework(fwPath) {
  const fwName = path.basename(fwPath).replace(/\.framework$/, '');
  const versionsDir = path.join(fwPath, 'Versions');
  if (!fs.existsSync(versionsDir)) return;

  const versions = fs
    .readdirSync(versionsDir)
    .filter((v) => v !== 'Current' && fs.statSync(path.join(versionsDir, v)).isDirectory());
  if (versions.length === 0) return;
  const realVersion = versions[0];

  // Versions/Current -> realVersion
  ensureSymlink(realVersion, path.join(versionsDir, 'Current'));

  // <fw>/<name>  -> Versions/Current/<name>
  const rootBinary = path.join(fwPath, fwName);
  if (fs.existsSync(rootBinary) || fs.lstatSync(rootBinary, { throwIfNoEntry: false })) {
    ensureSymlink(`Versions/Current/${fwName}`, rootBinary);
  }
  // <fw>/Resources -> Versions/Current/Resources
  const rootResources = path.join(fwPath, 'Resources');
  if (fs.existsSync(rootResources) || fs.lstatSync(rootResources, { throwIfNoEntry: false })) {
    ensureSymlink('Versions/Current/Resources', rootResources);
  }
}

function codesign(args) {
  const r = spawnSync('codesign', args, { encoding: 'utf8' });
  if (r.status !== 0) {
    const err = new Error(
      `codesign ${args.join(' ')} -> status ${r.status}\nstderr: ${r.stderr}\nstdout: ${r.stdout}`,
    );
    err.stderr = r.stderr;
    throw err;
  }
  return r;
}

// Look up an identity in the local keychain. Returns true when codesign
// can resolve it. Used to detect dev machines that don't have the
// Developer ID cert installed and fall back to ad-hoc signing instead of
// failing the whole build.
function identityResolves(identity) {
  if (!identity || identity === '-') return true;
  const r = spawnSync('codesign', ['--display', '--verbose=4', '--keychain', 'login.keychain', '--verify', '/Applications'], {
    encoding: 'utf8',
  });
  // The above is just to make sure the codesign tool works; we now ask the
  // system whether the identity matches anything in the keychain.
  const f = spawnSync('security', ['find-identity', '-v', '-p', 'codesigning'], {
    encoding: 'utf8',
  });
  return f.status === 0 && (f.stdout || '').includes(identity);
}

exports.default = async function afterSign(context) {
  if (context.electronPlatformName !== 'darwin') return;

  const appName = context.packager.appInfo.productFilename;
  const appPath = path.join(context.appOutDir, `${appName}.app`);
  const configuredIdentity = context.packager.platformSpecificBuildOptions.identity || '-';

  let identity = configuredIdentity;
  if (configuredIdentity !== '-' && !identityResolves(configuredIdentity)) {
    console.warn(
      `[afterSign] configured identity "${configuredIdentity}" not in keychain — ` +
        `falling back to ad-hoc signature for sidecar binaries. ` +
        `For shippable builds, install the Developer ID cert (or set CSC_LINK).`,
    );
    identity = '-';
  }

  // Sidecar binaries live under Contents/Resources/libexec/ per
  // docs/STRUCTURE.md §"Desktop Distribution" (valuz-server PyInstaller
  // bundle + rg helper). Contents/Resources/bin/ holds only the Go ``valuz``
  // CLI, which electron-builder signs through its main pass.
  const sidecarRoot = path.join(appPath, 'Contents', 'Resources', 'libexec');

  const frameworks = findFrameworks(sidecarRoot);
  for (const fw of frameworks) {
    console.log(`[afterSign] normalizing framework layout: ${fw}`);
    normalizeFramework(fw);
  }

  const machoFiles = findMachOFiles(sidecarRoot);
  console.log(`[afterSign] identity=${identity}`);
  console.log(`[afterSign] signing ${machoFiles.length} Mach-O files under ${sidecarRoot}`);

  let failed = 0;
  for (const f of machoFiles) {
    try {
      codesign(['--force', '--sign', identity, '--timestamp', '--options', 'runtime', f]);
    } catch (e) {
      failed += 1;
      console.error(`[afterSign] FAILED: ${f}\n${e.stderr || e.message}`);
    }
  }
  if (failed > 0) {
    throw new Error(`[afterSign] ${failed} sidecar Mach-O signatures failed (see above).`);
  }

  console.log(`[afterSign] re-sealing outer app: ${appPath}`);
  const entitlementsPath = path.join(__dirname, 'entitlements.mac.plist');
  try {
    codesign([
      '--force', '--sign', identity, '--timestamp', '--options', 'runtime',
      '--entitlements', entitlementsPath, appPath,
    ]);
  } catch (e) {
    console.error(`[afterSign] outer re-seal failed:\n${e.stderr || e.message}`);
    throw e;
  }

  // Notarize via Apple notarytool (requires APPLE_API_KEY_PATH, APPLE_API_KEY_ID,
  // APPLE_API_ISSUER env vars). Skip silently when credentials are absent — dev
  // and CI builds that don't set these will produce a valid but un-notarized DMG.
  if (process.env.APPLE_API_KEY_PATH && process.env.APPLE_API_KEY_ID && process.env.APPLE_API_ISSUER) {
    console.log(`[afterSign] notarizing ${appPath}...`);
    await notarize({
      tool: 'notarytool',
      appPath,
      appleApiKey: process.env.APPLE_API_KEY_PATH,
      appleApiKeyId: process.env.APPLE_API_KEY_ID,
      appleApiIssuer: process.env.APPLE_API_ISSUER,
    });
    console.log(`[afterSign] notarization complete`);
  } else {
    console.log(`[afterSign] skipping notarization (APPLE_API_KEY_PATH / APPLE_API_KEY_ID / APPLE_API_ISSUER not set)`);
  }
};

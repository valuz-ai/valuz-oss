const { execFileSync, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');
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

  // Each binary's existing entitlements are extracted and passed back
  // explicitly via --entitlements. --preserve-metadata=entitlements (PR #681)
  // is NOT enough: PyInstaller's automatic binary-vs-data reclassification
  // (build_main.py, "Performing binary vs. data reclassification") turns the
  // DATA-collected claude CLI into a BINARY and ad-hoc re-signs it, stripping
  // its entitlements long before this hook runs — preserve then has nothing
  // left to preserve. The SDK-bundled Claude Code CLI (Bun/JSC) needs
  // allow-jit + allow-unsigned-executable-memory; losing them under hardened
  // runtime kills JIT and the CLI dies on use with
  // "ReferenceError: SharedArrayBuffer is not defined".
  const entDir = fs.mkdtempSync(path.join(os.tmpdir(), 'aftersign-ent-'));
  const entitlementsFor = (file, index) => {
    // stdout capture, not `--entitlements <path>`: file-writing is exactly the
    // kind of codesign surface that drifted between macOS 15 and 26.
    const r = spawnSync('codesign', ['-d', '--entitlements', '-', '--xml', file], { encoding: 'utf8' });
    if (r.status !== 0) return null;
    const xml = (r.stdout || '').trim();
    if (!xml.includes('<key>')) return null;
    const out = path.join(entDir, `ent-${index}.plist`);
    fs.writeFileSync(out, xml);
    return out;
  };
  // Known JIT sidecars get their entitlements re-applied from a checked-in
  // copy: PyInstaller hands them over already stripped (see above), so
  // extracting from the binary itself has nothing to recover.
  const staticEntitlements = [
    {
      suffix: path.join('claude_agent_sdk', '_bundled', 'claude'),
      plist: path.join(__dirname, 'entitlements.claude-cli.plist'),
    },
  ];
  const staticEntitlementsFor = (file) => {
    const hit = staticEntitlements.find((s) => file.endsWith(s.suffix));
    return hit ? hit.plist : null;
  };
  // Record the claude sidecar's state BEFORE we touch it — distinguishes
  // "arrived stripped" from "our re-sign stripped it" in the CI log.
  const claudeSidecar = path.join(sidecarRoot, '_internal', 'claude_agent_sdk', '_bundled', 'claude');
  if (fs.existsSync(claudeSidecar)) {
    const pre = spawnSync('codesign', ['-dvv', claudeSidecar], { encoding: 'utf8' });
    const preState = ((pre.stderr || '') + (pre.stdout || ''))
      .split('\n')
      .filter((l) => /^(Authority=|Signature|Identifier=|CodeDirectory)/.test(l))
      .join(' | ');
    console.log(`[afterSign] claude pre-sign state: ${preState}`);
    console.log(
      `[afterSign] claude pre-sign entitlements: ${entitlementsFor(claudeSidecar, 'pre') ? 'present' : 'ABSENT'}`,
    );
  }

  let failed = 0;
  let withEntitlements = 0;
  machoFiles.forEach((f, i) => {
    try {
      const entFile = entitlementsFor(f, i) || staticEntitlementsFor(f);
      const args = ['--force', '--sign', identity, '--timestamp', '--options', 'runtime'];
      if (entFile) {
        withEntitlements += 1;
        args.push('--entitlements', entFile);
      }
      codesign([...args, f]);
      // Entitlements surviving the re-sign is the whole point — verify the
      // output instead of trusting codesign.
      if (entFile && entitlementsFor(f, machoFiles.length + i) === null) {
        throw new Error('entitlements missing after re-sign');
      }
    } catch (e) {
      failed += 1;
      console.error(`[afterSign] FAILED: ${f}\n${e.stderr || e.message}`);
    }
  });
  console.log(`[afterSign] ${withEntitlements} of ${machoFiles.length} sidecar binaries carry entitlements`);
  if (failed > 0) {
    throw new Error(`[afterSign] ${failed} sidecar Mach-O signatures failed (see above).`);
  }
  fs.rmSync(entDir, { recursive: true, force: true });

  // Sentinel: the Claude CLI is the binary users actually crash on when
  // entitlements get stripped — refuse to ship without allow-jit.
  if (fs.existsSync(claudeSidecar)) {
    const shown = spawnSync('codesign', ['-d', '--entitlements', '-', '--xml', claudeSidecar], { encoding: 'utf8' });
    if (!(shown.stdout || '').includes('com.apple.security.cs.allow-jit')) {
      throw new Error(
        '[afterSign] claude sidecar lost com.apple.security.cs.allow-jit after re-sign — ' +
          'this build would die with "ReferenceError: SharedArrayBuffer is not defined". Refusing to ship.',
      );
    }
    console.log('[afterSign] claude sidecar entitlements verified (allow-jit present)');
  } else {
    console.warn(`[afterSign] claude sidecar not found at ${claudeSidecar} — entitlement sentinel skipped`);
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

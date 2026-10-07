const { spawnSync } = require('child_process');

// A valid signature and JIT entitlements do not prove that the bundled
// executable can start (for example, a CPU/runtime incompatibility can hang
// before the CLI even handles --version). This probe uses no model or account.
function verifyClaudeCli(cliPath, timeoutMs = 30000) {
  const result = spawnSync(cliPath, ['--version'], {
    encoding: 'utf8',
    timeout: timeoutMs,
    killSignal: 'SIGKILL',
    maxBuffer: 64 * 1024,
  });
  const version = (result.stdout || '').trim();
  if (result.error || result.status !== 0 || !/^\d+\.\d+\.\d+\b/.test(version)) {
    const reason = result.error?.code || result.signal || `exit ${result.status}`;
    throw new Error(
      `[afterSign] bundled Claude CLI cannot start (${reason}); ` +
        'verify the signed executable on this build host and target architecture before shipping.',
    );
  }
  return version;
}

module.exports = { verifyClaudeCli };

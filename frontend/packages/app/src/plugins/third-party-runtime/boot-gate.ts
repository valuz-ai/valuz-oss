/**
 * "The first-party plugins have settled" (doc 04 §4.2): the OSS plugins, the
 * edition overlay and the first render are done. Hosts call
 * {@link markThirdPartyBootSettled} then; third-party plugins start loading
 * only after it, so they never slow down or block the app's start.
 */
let settled = false;
const waiters = new Set<() => void>();

export function markThirdPartyBootSettled(): void {
  if (settled) return;
  settled = true;
  for (const resolve of [...waiters]) resolve();
  waiters.clear();
}

/** Resolves once the boot has settled (immediately if it already has). */
export function whenThirdPartyBootSettled(signal?: AbortSignal): Promise<void> {
  if (settled) return Promise.resolve();
  return new Promise<void>((resolve) => {
    const done = () => {
      signal?.removeEventListener("abort", done);
      waiters.delete(done);
      resolve();
    };
    waiters.add(done);
    signal?.addEventListener("abort", done, { once: true });
  });
}

export function isThirdPartyBootSettled(): boolean {
  return settled;
}

/** Test seam: forget that the boot settled. */
export function resetThirdPartyBootGate(): void {
  settled = false;
  waiters.clear();
}

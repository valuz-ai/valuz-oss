/** Move crash protection state written before App Plugin naming was introduced. */
export function migrateLegacyAppPluginCrashState(
  storage: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null,
): void {
  for (const suffix of ["loading", "crashes"]) {
    const legacyKey = `valuz.thirdParty.${suffix}`;
    const canonicalKey = `valuz.appPlugins.${suffix}`;
    try {
      const legacy = storage?.getItem(legacyKey);
      if (legacy != null && storage?.getItem(canonicalKey) == null) {
        storage?.setItem(canonicalKey, legacy);
      }
      storage?.removeItem(legacyKey);
    } catch {
      // Storage denial must not prevent startup; the crash guard is fail-open.
    }
  }
}

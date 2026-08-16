import {
  chmodSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import type { EgressMode, PublicEgressMode } from "../contracts";

const FILE_NAME = "network-egress.json";

export const readPersistedEgressMode = (
  userDataDir: string,
): PublicEgressMode | null => {
  try {
    const value = JSON.parse(
      readFileSync(join(userDataDir, FILE_NAME), "utf8"),
    ) as { mode?: unknown; compatibilityMode?: unknown };
    if (value.mode === "off" || value.mode === "auto") return value.mode;
    if (typeof value.compatibilityMode === "boolean") {
      return value.compatibilityMode ? "off" : "auto";
    }
    return null;
  } catch {
    return null;
  }
};

export const writePersistedEgressMode = (
  userDataDir: string,
  mode: EgressMode,
): void => {
  const path = join(userDataDir, FILE_NAME);
  const temporary = `${path}.tmp`;
  mkdirSync(dirname(path), { recursive: true });
  const persistedMode: PublicEgressMode = mode === "off" ? "off" : "auto";
  writeFileSync(
    temporary,
    JSON.stringify({
      version: 1,
      mode: persistedMode,
      // Keep the legacy field so a rollback preserves the user's owner choice.
      compatibilityMode: persistedMode === "off",
    }),
    { encoding: "utf8", mode: 0o600 },
  );
  renameSync(temporary, path);
  chmodSync(path, 0o600);
};

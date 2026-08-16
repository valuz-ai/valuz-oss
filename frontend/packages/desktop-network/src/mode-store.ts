import {
  chmodSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import type { EgressMode } from "./types";

const FILE_NAME = "network-egress.json";

export const readPersistedEgressMode = (userDataDir: string): EgressMode => {
  try {
    const value = JSON.parse(
      readFileSync(join(userDataDir, FILE_NAME), "utf8"),
    ) as { compatibilityMode?: boolean };
    return value.compatibilityMode === true ? "off" : "auto";
  } catch {
    // The capability is available without a launch flag, but new installs
    // opt in from Settings after reviewing the two connection owners.
    return "off";
  }
};

export const writePersistedEgressMode = (
  userDataDir: string,
  mode: EgressMode,
): void => {
  const path = join(userDataDir, FILE_NAME);
  const temporary = `${path}.tmp`;
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(
    temporary,
    JSON.stringify({ compatibilityMode: mode === "off" }),
    { encoding: "utf8", mode: 0o600 },
  );
  renameSync(temporary, path);
  chmodSync(path, 0o600);
};

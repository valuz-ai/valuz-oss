import {
  chmodSync,
  lstatSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { isAbsolute, dirname } from "node:path";
import type { EgressBootstrap } from "./control-server";

const MAX_BOOTSTRAP_BYTES = 16 * 1024;

/** Publish a dev-only one-shot bootstrap into a launcher-owned 0700 directory. */
export const publishDevEgressBootstrap = (
  filePath: string,
  bootstrap: EgressBootstrap,
): void => {
  if (!isAbsolute(filePath)) throw new Error("invalid_egress_bootstrap_file");
  const parent = lstatSync(dirname(filePath));
  if (
    !parent.isDirectory() ||
    parent.isSymbolicLink() ||
    (parent.mode & 0o077) !== 0 ||
    (typeof process.getuid === "function" && parent.uid !== process.getuid())
  ) {
    throw new Error("insecure_egress_bootstrap_directory");
  }
  let targetExists = true;
  try {
    lstatSync(filePath);
  } catch (error) {
    if (error instanceof Error && "code" in error && error.code === "ENOENT") {
      targetExists = false;
    } else {
      throw error;
    }
  }
  if (targetExists) throw new Error("egress_bootstrap_file_exists");

  const payload = `${JSON.stringify(bootstrap)}\n`;
  if (Buffer.byteLength(payload) > MAX_BOOTSTRAP_BYTES) {
    throw new Error("egress_bootstrap_payload_too_large");
  }
  const temporary = `${filePath}.${process.pid}.tmp`;
  try {
    writeFileSync(temporary, payload, {
      encoding: "utf8",
      flag: "wx",
      mode: 0o600,
    });
    chmodSync(temporary, 0o600);
    renameSync(temporary, filePath);
    chmodSync(filePath, 0o600);
  } catch (error) {
    try {
      unlinkSync(temporary);
    } catch {
      // The temporary may never have been created or may already be renamed.
    }
    throw error;
  }
};

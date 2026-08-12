import type {
  A2UIThemeExtension,
  A2UIThemeMode,
  A2UIThemeTokens,
} from "./types";

const extensions = new Map<string, A2UIThemeExtension>();
const listeners = new Set<() => void>();
let version = 0;

function publish() {
  version += 1;
  listeners.forEach((listener) => listener());
}

function assertTokenMap(
  extension: A2UIThemeExtension,
  kind: "tokens" | "overrides",
) {
  const maps = extension[kind];
  for (const mode of ["light", "dark"] as const) {
    for (const [token, value] of Object.entries(maps?.[mode] ?? {})) {
      if (!token.startsWith("--va2-")) {
        throw new Error(`A2UI theme "${extension.id}" has invalid token "${token}"`);
      }
      if (kind === "tokens" && !token.startsWith(`--va2-${extension.id}-`)) {
        throw new Error(
          `A2UI theme "${extension.id}" must namespace new token "${token}" as "--va2-${extension.id}-*"`,
        );
      }
      if (typeof value !== "string" || !value.trim()) {
        throw new Error(`A2UI theme "${extension.id}" has an empty value for "${token}"`);
      }
    }
  }
}

function assertExtension(extension: A2UIThemeExtension) {
  if (!/^[a-z][a-z0-9-]*$/.test(extension.id)) {
    throw new Error(`A2UI theme extension id "${extension.id}" must be a lowercase slug`);
  }
  if (extension.id === "default") {
    throw new Error('A2UI theme extension id "default" is reserved');
  }
  if (extension.extends?.includes(extension.id)) {
    throw new Error(`A2UI theme "${extension.id}" cannot extend itself`);
  }
  assertTokenMap(extension, "tokens");
  assertTokenMap(extension, "overrides");
}

/**
 * Register a distribution-owned A2UI theme contribution.
 *
 * Re-registering an id replaces it for deterministic HMR. The returned
 * disposer removes only the exact contribution that created it.
 */
export function registerA2UIThemeExtension(
  extension: A2UIThemeExtension,
): () => void {
  assertExtension(extension);
  extensions.set(extension.id, extension);
  publish();
  return () => {
    if (extensions.get(extension.id) !== extension) return;
    extensions.delete(extension.id);
    publish();
  };
}

export function getA2UIThemeExtensions(): A2UIThemeExtension[] {
  return [...extensions.values()];
}

export function getA2UIThemeRegistryVersion(): number {
  return version;
}

export function subscribeA2UIThemeExtensions(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function orderedExtensions(): A2UIThemeExtension[] {
  const ordered: A2UIThemeExtension[] = [];
  const visiting = new Set<string>();
  const visited = new Set<string>();

  function visit(extension: A2UIThemeExtension) {
    if (visited.has(extension.id)) return;
    if (visiting.has(extension.id)) {
      throw new Error(`A2UI theme inheritance contains a cycle at "${extension.id}"`);
    }
    visiting.add(extension.id);
    for (const parentId of extension.extends ?? ["default"]) {
      if (parentId === "default") continue;
      const parent = extensions.get(parentId);
      if (!parent) {
        throw new Error(`A2UI theme "${extension.id}" extends missing theme "${parentId}"`);
      }
      visit(parent);
    }
    visiting.delete(extension.id);
    visited.add(extension.id);
    ordered.push(extension);
  }

  extensions.forEach(visit);
  return ordered;
}

export function resolveA2UIThemeTokens(mode: A2UIThemeMode): A2UIThemeTokens {
  const resolved: Partial<A2UIThemeTokens> = {};
  for (const extension of orderedExtensions()) {
    Object.assign(resolved, extension.tokens?.[mode], extension.overrides?.[mode]);
  }
  return resolved as A2UIThemeTokens;
}

/** Test-only reset; kept out of the package barrel. */
export function resetA2UIThemeExtensionsForTests() {
  if (extensions.size === 0) return;
  extensions.clear();
  publish();
}

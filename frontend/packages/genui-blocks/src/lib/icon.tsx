"use client";

import dynamicIconImports from "lucide-react/dynamicIconImports.mjs";
import { createElement, useEffect, useState } from "react";

/**
 * Icons by name.
 *
 * Blocks render from model output, so the icon arrives as a string. The two
 * obvious implementations both fail: importing lucide's barrel pulls ~1900
 * components into the bundle, and a hand-curated map means the model can only
 * name icons someone remembered to add.
 *
 * lucide ships `dynamicIconImports` — a map of name → `() => import(...)`, one
 * static import expression per icon — so the bundler code-splits every icon and
 * a page loads only the few a document uses. This wraps that map.
 *
 * It renders each icon's `__iconNode` (lucide's path data) into an `<svg>`
 * rather than lazily mounting lucide's own component. Rendering data avoids
 * minting a React component during render, which is both a lint error and a
 * real remount hazard if the memoisation behind it ever slipped.
 *
 * Names are lucide's kebab-case ids (`trending-up`, `dollar-sign`). An unknown
 * name renders nothing: the model will invent names, and neither a thrown error
 * nor a broken glyph should reach a generated document.
 */

/** lucide's shape: a list of `[svgTag, attributes]`. */
type IconNode = [string, Record<string, string | number>][];

type IconModule = { __iconNode?: IconNode };
type IconLoaders = Record<string, () => Promise<IconModule>>;

const loaders = dynamicIconImports as unknown as IconLoaders;

// Resolved path data, keyed by name. `null` records a name that does not exist
// so a bad name is looked up once rather than on every render.
const nodeCache = new Map<string, IconNode | null>();

/**
 * Accept any spelling of a lucide name.
 *
 * The prompt just names lucide-react, which the model knows from pretraining —
 * but it knows it as the *component* export, `TrendingUp` / `Building2`, while
 * the dynamic import map is keyed on the id, `trending-up` / `building-2`.
 * Insisting on one spelling would make a correct icon name render nothing, so
 * PascalCase, snake_case and spaced forms all fold onto the id. The digit
 * boundary matters as much as the case one: lucide has a `Building2`.
 */
function normalise(name: string | undefined): string {
  return (name ?? "")
    .trim()
    .replace(/([a-z])([A-Z])/g, "$1-$2")
    .replace(/([a-zA-Z])([0-9])/g, "$1-$2")
    .replace(/[\s_]+/g, "-")
    .toLowerCase()
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

/** True when `name` is an icon lucide actually ships. */
export function isKnownIcon(name: string | undefined): boolean {
  const key = normalise(name);
  return Boolean(key) && key in loaders;
}

export interface BlockIconProps {
  /** lucide kebab-case name, e.g. "trending-up". */
  name: string | undefined;
  /** Any CSS length; defaults to 1em so the icon tracks the type around it. */
  size?: number | string;
  className?: string;
}

export function BlockIcon({ name, size = "1em", className }: BlockIconProps) {
  const key = normalise(name);
  // A cache hit is known during render, so it is read here rather than pushed
  // through state — setting state for something already in hand costs a second
  // render of every icon on the page.
  // Whether the name exists is knowable during render, so an invented name
  // never starts an effect at all.
  const known = Boolean(key) && key in loaders;
  const cached = nodeCache.get(key);
  // Only the async path needs state, and it carries the key it belongs to so a
  // changed name never shows the previous icon while the new one loads.
  const [loaded, setLoaded] = useState<{ key: string; node: IconNode | null } | null>(null);
  const node = !known
    ? null
    : cached !== undefined
      ? cached
      : loaded?.key === key
        ? loaded.node
        : null;

  useEffect(() => {
    if (!known || nodeCache.has(key)) return;
    const loader = loaders[key];
    if (!loader) return;
    let cancelled = false;
    void loader()
      .then((module) => {
        const resolved = module.__iconNode ?? null;
        nodeCache.set(key, resolved);
        if (!cancelled) setLoaded({ key, node: resolved });
      })
      .catch(() => {
        // A chunk that fails to load is not worth surfacing: the icon is
        // decorative and the text it marks is already on screen.
        nodeCache.set(key, null);
        if (!cancelled) setLoaded({ key, node: null });
      });
    return () => {
      cancelled = true;
    };
  }, [key, known]);

  if (!node) return null;

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      // Always decorative: every block carrying an icon also carries the text
      // it marks, so announcing it would only duplicate that text.
      aria-hidden={true}
      focusable={false}
    >
      {node.map(([tag, attrs], index) => createElement(tag, { ...attrs, key: index }))}
    </svg>
  );
}

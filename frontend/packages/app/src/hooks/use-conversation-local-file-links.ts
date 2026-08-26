import { createContext, useCallback, useContext } from "react";
import { parseFileRef } from "@valuz/shared";

import { isAbsolutePath, toProjectRelativePath } from "../lib/project-paths";
import type { ArtifactOpenTarget } from "@valuz/ui";

export interface ConversationLocalFileLinkOptions {
  projectRootPath: string;
  runtimeMode?: "local" | "managed";
  previewFile: (path: string, target?: ArtifactOpenTarget) => void;
  openFile: (path: string) => void;
  blockFile?: (path: string, reason: ConversationLocalFileBlockReason) => void;
}

export type ConversationLocalFileBlockReason =
  "managed_outside_project" | "unsupported";

export type ConversationLocalFileLinkResolution =
  | { kind: "preview"; path: string; target?: ArtifactOpenTarget }
  | { kind: "open"; path: string }
  | {
      kind: "blocked";
      path: string;
      reason: ConversationLocalFileBlockReason;
    };

export interface ConversationLocalFileLinkController {
  resolveLocalFileHref: (
    href: string,
  ) => ConversationLocalFileLinkResolution | null;
  isLocalFileHref: (href: string) => boolean;
  openLocalFileHref: (href: string) => void;
}

export interface ConversationLocalFileLinkOverride {
  resolveLocalFileHref?: (
    href: string,
    context: ConversationLocalFileLinkOptions,
    fallback: ConversationLocalFileLinkController,
  ) => ConversationLocalFileLinkResolution | null;
  isLocalFileHref: (
    href: string,
    context: ConversationLocalFileLinkOptions,
  ) => boolean;
  openLocalFileHref: (
    href: string,
    context: ConversationLocalFileLinkOptions,
    fallback: ConversationLocalFileLinkController,
  ) => void;
}

export const ConversationLocalFileLinkOverrideContext =
  createContext<ConversationLocalFileLinkOverride | null>(null);

export function useConversationLocalFileLinks(
  options: ConversationLocalFileLinkOptions,
): ConversationLocalFileLinkController {
  const override = useContext(ConversationLocalFileLinkOverrideContext);
  const fallback = useDefaultConversationLocalFileLinks(options);

  const resolveLocalFileHref = useCallback(
    (href: string) =>
      override?.resolveLocalFileHref?.(href, options, fallback) ??
      fallback.resolveLocalFileHref(href),
    [fallback, options, override],
  );

  const isLocalFileHref = useCallback(
    (href: string) =>
      override?.isLocalFileHref(href, options) ??
      fallback.isLocalFileHref(href),
    [fallback, options, override],
  );

  const openLocalFileHref = useCallback(
    (href: string) => {
      if (override) {
        override.openLocalFileHref(href, options, fallback);
        return;
      }
      fallback.openLocalFileHref(href);
    },
    [fallback, options, override],
  );

  return { resolveLocalFileHref, isLocalFileHref, openLocalFileHref };
}

function useDefaultConversationLocalFileLinks(
  options: ConversationLocalFileLinkOptions,
): ConversationLocalFileLinkController {
  const resolveLocalFileHref = useCallback(
    (href: string) =>
      resolveDefaultLocalFileHref(
        href,
        options.projectRootPath,
        options.runtimeMode,
      ),
    [options.projectRootPath, options.runtimeMode],
  );

  const isLocalFileHref = useCallback(
    (href: string) => resolveLocalFileHref(href) !== null,
    [resolveLocalFileHref],
  );

  const openLocalFileHref = useCallback(
    (href: string) => {
      const resolution = resolveLocalFileHref(href);
      if (!resolution) return;
      if (resolution.kind === "preview") {
        if (resolution.target) {
          options.previewFile(resolution.path, resolution.target);
        } else {
          options.previewFile(resolution.path);
        }
        return;
      }
      if (resolution.kind === "open") {
        options.openFile(resolution.path);
        return;
      }
      options.blockFile?.(resolution.path, resolution.reason);
    },
    [options, resolveLocalFileHref],
  );

  return { resolveLocalFileHref, isLocalFileHref, openLocalFileHref };
}

export function isDefaultLocalFileHref(
  href: string,
  projectRootPath: string,
  runtimeMode: "local" | "managed" = "local",
): boolean {
  return (
    resolveDefaultLocalFileHref(href, projectRootPath, runtimeMode) !== null
  );
}

export function resolveDefaultLocalFileHref(
  href: string,
  projectRootPath: string,
  runtimeMode: "local" | "managed" = "local",
): ConversationLocalFileLinkResolution | null {
  const isFileHref = href.toLowerCase().startsWith("file://");
  const target = parseArtifactOpenTarget(href);
  const path = normalizeLocalFileHref(href);
  if (!path || (!isFileHref && /^[a-z][a-z0-9+.-]*:/i.test(path))) {
    return null;
  }

  const relative = toGuardedProjectRelativePath(path, projectRootPath);
  if (relative) {
    return target
      ? { kind: "preview", path: relative, target }
      : { kind: "preview", path: relative };
  }

  if (runtimeMode === "managed") {
    if (isFileHref || isAbsolutePath(path)) {
      return {
        kind: "blocked",
        path,
        reason: "managed_outside_project",
      };
    }
    return null;
  }

  if (isFileHref || /^[a-zA-Z]:[\\/]/.test(path)) {
    return { kind: "open", path };
  }

  if (
    path.startsWith("/") &&
    /^\/(Users|home|tmp|var|private|opt|Volumes|mnt|workspace|workspaces)\//.test(
      path,
    )
  ) {
    return { kind: "open", path };
  }

  if (
    path.startsWith("./") ||
    /^[^?#]+[\\/][^\\/]+\.[a-zA-Z0-9]{1,12}$/.test(path) ||
    /^[^?#]+\.[a-zA-Z0-9]{1,12}$/.test(path)
  ) {
    const previewPath = path.replace(/^\.\//, "");
    return target
      ? { kind: "preview", path: previewPath, target }
      : { kind: "preview", path: previewPath };
  }

  return null;
}

export function parseArtifactOpenTarget(
  href: string,
): ArtifactOpenTarget | null {
  const trimmed = href.trim();
  if (!trimmed) return null;

  const hashIndex = trimmed.indexOf("#");
  const queryIndex = trimmed.indexOf("?");
  const fragment = hashIndex === -1 ? "" : trimmed.slice(hashIndex + 1);
  const hasQuery =
    queryIndex !== -1 && (hashIndex === -1 || queryIndex < hashIndex);
  const query = hasQuery
    ? trimmed.slice(queryIndex + 1, hashIndex === -1 ? undefined : hashIndex)
    : "";
  const rawPage =
    new URLSearchParams(fragment).get("page") ??
    new URLSearchParams(query).get("page");
  if (!rawPage || !/^[1-9]\d*$/.test(rawPage)) return null;

  const page = Number(rawPage);
  return Number.isSafeInteger(page) ? { page } : null;
}

export function normalizeLocalFileHref(href: string): string {
  const trimmed = href.trim();
  if (!trimmed) return "";

  const withoutFragment = trimmed.split("#", 1)[0].split("?", 1)[0];
  let path = withoutFragment;

  // ``valuz-file://<abs>`` (the model/artifact file scheme) and ``file://`` are
  // stripped to the absolute path so the usual cwd-relative logic routes them to
  // preview. See docs/design/file-address-resolution.md.
  const lower = withoutFragment.toLowerCase();
  if (lower.startsWith("valuz-file://")) {
    // Delegate to the single shared parser (tolerant — a model may emit a
    // two-slash ref). It returns the DECODED absolute path, so finish here
    // instead of re-decoding below.
    const parsed = parseFileRef(withoutFragment);
    if (parsed) return stripMarkdownLineSuffix(parsed);
    path = withoutFragment.replace(/^valuz-file:\/\//i, "");
  } else if (lower.startsWith("file://")) {
    try {
      const url = new URL(withoutFragment);
      // Tolerate a two-slash href — fold the mis-parsed host back onto the path.
      path = (url.host ? `/${url.host}` : "") + url.pathname;
      if (/^\/[a-zA-Z]:\//.test(path)) path = path.slice(1);
    } catch {
      path = withoutFragment.replace(/^file:\/\//i, "");
    }
  }

  try {
    path = decodeURIComponent(path);
  } catch {
    try {
      path = decodeURI(path);
    } catch {
      // Keep the original path when percent-decoding is malformed.
    }
  }

  return stripMarkdownLineSuffix(path);
}

function stripMarkdownLineSuffix(path: string): string {
  return path.replace(/:(\d+)(?::\d+)?$/, "");
}

/**
 * The shared root arithmetic plus this surface's traversal guards: a prose
 * link that walks out of the project (``../``) must not resolve to a preview.
 */
function toGuardedProjectRelativePath(
  path: string,
  rootPath: string,
): string | null {
  const normalized = path.replace(/\\/g, "/").replace(/^\.\//, "");
  if (!normalized || normalized.startsWith("../")) return null;
  const relative = toProjectRelativePath(normalized, rootPath);
  return relative && !relative.startsWith("../") ? relative : null;
}

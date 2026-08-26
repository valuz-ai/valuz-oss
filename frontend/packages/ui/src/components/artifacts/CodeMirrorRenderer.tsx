import { EditorState, type Extension } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { basicSetup } from "codemirror";
import { useEffect, useMemo, useRef, useState } from "react";

import type { ArtifactRendererProps } from "./artifact-viewer.types";
import "./CodeMirrorRenderer.css";

import { useI18n } from "../../hooks/use-i18n";

async function languageForPath(path: string): Promise<Extension[]> {
  const extension = path.split(".").pop()?.toLowerCase() ?? "";
  switch (extension) {
    case "ts": {
      const { javascript } = await import("@codemirror/lang-javascript");
      return [javascript({ typescript: true })];
    }
    case "tsx": {
      const { javascript } = await import("@codemirror/lang-javascript");
      return [javascript({ jsx: true, typescript: true })];
    }
    case "js": {
      const { javascript } = await import("@codemirror/lang-javascript");
      return [javascript()];
    }
    case "jsx": {
      const { javascript } = await import("@codemirror/lang-javascript");
      return [javascript({ jsx: true })];
    }
    case "py": {
      const { python } = await import("@codemirror/lang-python");
      return [python()];
    }
    case "json": {
      const { json } = await import("@codemirror/lang-json");
      return [json()];
    }
    case "yaml":
    case "yml": {
      const { yaml } = await import("@codemirror/lang-yaml");
      return [yaml()];
    }
    case "html":
    case "htm": {
      const { html } = await import("@codemirror/lang-html");
      return [html()];
    }
    case "css":
    case "scss": {
      const { css } = await import("@codemirror/lang-css");
      return [css()];
    }
    case "md":
    case "markdown": {
      const { markdown } = await import("@codemirror/lang-markdown");
      return [markdown()];
    }
    case "sql": {
      const { sql } = await import("@codemirror/lang-sql");
      return [sql()];
    }
    default:
      return [];
  }
}

export function CodeMirrorRenderer({
  artifact,
  content,
  wrapLines = false,
}: ArtifactRendererProps) {
  const { t } = useI18n();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [languageExtensions, setLanguageExtensions] = useState<Extension[]>([]);
  const sourcePath = artifact.path ?? artifact.name;

  useEffect(() => {
    let cancelled = false;
    setLanguageExtensions([]);
    void languageForPath(sourcePath)
      .then((extensions) => {
        if (!cancelled) setLanguageExtensions(extensions);
      })
      .catch(() => {
        // Syntax highlighting is an enhancement; keep the readable editor if a
        // language chunk cannot be loaded.
        if (!cancelled) setLanguageExtensions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [sourcePath]);
  const extensions = useMemo<Extension[]>(
    () => [
      basicSetup,
      EditorState.readOnly.of(true),
      EditorView.contentAttributes.of({
        spellcheck: "false",
        autocorrect: "off",
        autocapitalize: "off",
        translate: "no",
      }),
      ...(artifact.previewKind === "plain" || wrapLines
        ? [EditorView.lineWrapping]
        : []),
      EditorView.theme({
        "&": {
          height: "100%",
          backgroundColor: "transparent",
          color: "var(--color-ink-heading)",
        },
        ".cm-scroller": {
          fontFamily:
            "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
          fontSize: "12px",
          lineHeight: "1.6",
        },
        ".cm-gutters": {
          backgroundColor: "var(--color-surface-soft)",
          borderRight: "1px solid var(--color-surface-border)",
          color: "var(--color-ink-meta)",
        },
        ".cm-activeLine": {
          backgroundColor:
            "color-mix(in srgb, var(--color-primary) 6%, transparent)",
        },
        ".cm-activeLineGutter": {
          backgroundColor:
            "color-mix(in srgb, var(--color-primary) 6%, transparent)",
        },
        ".cm-selectionBackground": {
          backgroundColor:
            "color-mix(in srgb, var(--color-primary) 18%, transparent) !important",
        },
        ".cm-content": { padding: "16px 0" },
        ".cm-line": { padding: "0 16px" },
      }),
      ...languageExtensions,
    ],
    [artifact.previewKind, languageExtensions, wrapLines],
  );

  useEffect(() => {
    if (!containerRef.current || content?.kind !== "text") return;
    const view = new EditorView({
      parent: containerRef.current,
      state: EditorState.create({
        doc: content.content,
        extensions,
      }),
    });
    return () => view.destroy();
  }, [content, extensions]);

  if (content?.kind !== "text") {
    return (
      <div className="flex h-full items-center justify-center text-sm text-ink-meta">
        {t("ui.artifact.textReadFailed")}
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface-base">
      <div
        ref={containerRef}
        className={`min-h-0 flex-1 overflow-hidden ${
          artifact.previewKind === "markdown" ? "valuz-markdown-source" : ""
        }`}
      />
      {content.truncated ? (
        <div className="border-t border-surface-border bg-warning-light px-4 py-2 text-xs text-warning-text">
          {t("ui.artifact.truncated")}
        </div>
      ) : null}
    </div>
  );
}

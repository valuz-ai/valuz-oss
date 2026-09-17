/**
 * Content-conditional Streamdown plugins, loaded only when the markdown
 * actually needs them.
 *
 * ``mermaid`` and ``math`` are the two heaviest things the transcript renderer
 * can pull in, and both are needed only when a document happens to contain a
 * diagram or a formula. Measured on the webui entry chunk (sourcemap
 * attribution, v0.6.0-30-g8d03c457): mermaid 714 KB and katex 596 KB of source,
 * 13.2% of the entry — paid on first paint by every reader of every transcript,
 * the overwhelming majority of which contain neither.
 *
 * ``code`` and ``cjk`` stay static on purpose. Code blocks are in almost every
 * transcript, so deferring them would trade a real size win for a guaranteed
 * re-render; and Shiki already splits its grammars into per-language chunks, so
 * the always-loaded part is small. ``cjk`` is a remark plugin measured in
 * kilobytes and changes how ordinary prose tokenises — deferring it would make
 * plain text visibly reflow.
 *
 * ``PluginConfig`` declares every key optional, so a map without ``mermaid`` /
 * ``math`` is a supported configuration rather than a hole we are punching.
 */

import { useEffect, useMemo, useState } from "react";
import type { PluginConfig } from "streamdown";

/**
 * A fenced block whose info string opens with ``mermaid``.
 *
 * Anchored to the fence so the word "mermaid" in prose does not trigger a
 * download. Tolerates leading whitespace (a fence nested in a list item) and
 * both fence characters. During streaming the info string arrives with the
 * opening fence, well before the diagram body, so the import starts early.
 */
const MERMAID_FENCE = /^[ \t]*(?:`{3,}|~{3,})[ \t]*mermaid\b/m;

/**
 * Anything that might be TeX.
 *
 * Deliberately permissive, because the two failure directions are not
 * symmetric: a false positive downloads a chunk nobody looks at, while a false
 * negative renders someone's formula as literal ``$x^2$`` forever. So this
 * matches display math, backslash delimiters, and any single-line ``$…$`` pair
 * that does not open with whitespace — the rule KaTeX itself uses to tell
 * inline math from a currency amount. Prose with two dollar amounts on one line
 * ("$100 and $200") matches and costs one wasted chunk; a lone "$50" does not.
 */
const MATH_DELIMITER = /\$\$[\s\S]*?\$\$|\$[^\s$][^$\n]{0,400}\$|\\\(|\\\[/;

type HeavyPlugins = Pick<PluginConfig, "mermaid" | "math">;

/** Module-level so a second message with a diagram reuses the first import. */
let mermaidPromise: Promise<HeavyPlugins["mermaid"]> | undefined;
let mathPromise: Promise<HeavyPlugins["math"]> | undefined;

const loadMermaid = () => {
  mermaidPromise ??= import("@streamdown/mermaid").then((m) => m.mermaid);
  return mermaidPromise;
};

const loadMath = () => {
  // Imports the KaTeX stylesheet alongside the plugin — see ./markdown-math.ts
  // for why the CSS cannot stay at the top of MarkdownContent.
  mathPromise ??= import("./markdown-math").then((m) => m.math);
  return mathPromise;
};

/**
 * Plugins this particular markdown source needs, populated as they arrive.
 *
 * Returns an empty map on the first render of a document that needs one, so
 * callers must tolerate a diagram briefly rendering as a code block and a
 * formula briefly rendering as its source. That flash is the cost of the win
 * and only ever appears on content that actually has diagrams or math.
 */
export function useHeavyMarkdownPlugins(content: string): HeavyPlugins {
  const wantsMermaid = useMemo(() => MERMAID_FENCE.test(content), [content]);
  const wantsMath = useMemo(() => MATH_DELIMITER.test(content), [content]);
  const [plugins, setPlugins] = useState<HeavyPlugins>({});

  useEffect(() => {
    if (!wantsMermaid) return;
    let live = true;
    void loadMermaid().then((mermaid) => {
      if (live)
        setPlugins((prev) => (prev.mermaid ? prev : { ...prev, mermaid }));
    });
    return () => {
      live = false;
    };
  }, [wantsMermaid]);

  useEffect(() => {
    if (!wantsMath) return;
    let live = true;
    void loadMath().then((math) => {
      if (live) setPlugins((prev) => (prev.math ? prev : { ...prev, math }));
    });
    return () => {
      live = false;
    };
  }, [wantsMath]);

  // Hand back only what this document asked for. Keeping a plugin that a later
  // edit removed the need for would be harmless but makes the rendered output
  // depend on what the component happened to render earlier.
  return useMemo(
    () => ({
      ...(wantsMermaid && plugins.mermaid ? { mermaid: plugins.mermaid } : {}),
      ...(wantsMath && plugins.math ? { math: plugins.math } : {}),
    }),
    [wantsMermaid, wantsMath, plugins],
  );
}

/** Test seam: drop the module-level caches between cases. */
export function __resetHeavyPluginCache() {
  mermaidPromise = undefined;
  mathPromise = undefined;
}

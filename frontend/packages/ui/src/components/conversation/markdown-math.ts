/**
 * The math plugin and its stylesheet, together in one lazily-imported module.
 *
 * KaTeX ships ~25 KB of CSS that is useless without the plugin and inert with
 * it absent. Left as a top-level ``import "katex/dist/katex.min.css"`` in
 * MarkdownContent it lands in the entry stylesheet, so deferring only the JS
 * would still make every reader download the CSS on first paint. Pairing them
 * here keeps "needs math" a single decision with a single chunk.
 */

import "katex/dist/katex.min.css";

export { math } from "@streamdown/math";

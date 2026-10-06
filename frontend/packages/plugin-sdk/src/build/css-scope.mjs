// CSS scoping for plugin stylesheets (doc 02 §2.3): every selector of a
// plugin's own CSS is limited to the plugin's root element
// ``[data-valuz-ext="<plugin id>"]`` so it cannot restyle the host.
//
// A small, forgiving tokenizer rather than a full CSS parser: it understands
// strings, comments, escapes, parentheses / brackets and blocks, which is all
// the scoping needs. Rules:
//   - top-level style rules and style rules inside conditional group at-rules
//     (@media, @supports, @container, @layer, @document, @scope,
//     @starting-style) get every selector of their list prefixed;
//   - rules nested inside a style rule (CSS nesting) are relative to it and
//     stay as they are;
//   - every other block at-rule (@keyframes, @font-face, @page, @property,
//     @counter-style, @font-feature-values, @view-transition, unknown ones)
//     is copied verbatim;
//   - statements (@import, @charset, @layer a, b;, declarations) are copied;
//   - ``:root`` / ``html`` / ``body`` alone become the scope itself;
//     ``:root .x`` → ``<scope> .x``; a root compound with qualifiers
//     (``html.dark``, ``:root.dark``) and the host's theme class ``.dark``
//     stay in front: ``.dark .x`` → ``.dark <scope> .x``, because the host
//     puts them on <html>, an ancestor of the plugin root.

const GROUP_AT_RULES = new Set([
  "media",
  "supports",
  "container",
  "layer",
  "document",
  "-moz-document",
  "scope",
  "starting-style",
]);

/** The scope selector of plugin ``pluginId``. */
export function scopeSelector(pluginId) {
  const value = String(pluginId).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  return `[data-valuz-ext="${value}"]`;
}

/** Index just past the comment starting at ``i`` (``/*``). */
function skipComment(css, i) {
  const end = css.indexOf("*/", i + 2);
  return end === -1 ? css.length : end + 2;
}

/** Index just past the string starting at ``i`` (quote char at ``i``). */
function skipString(css, i) {
  const quote = css[i];
  let j = i + 1;
  while (j < css.length) {
    const ch = css[j];
    if (ch === "\\") {
      j += 2;
      continue;
    }
    if (ch === quote || ch === "\n") return j + 1;
    j += 1;
  }
  return css.length;
}

/**
 * The index of the first ``{``, ``;`` or ``}`` at nesting depth 0 (outside
 * strings, comments, parentheses and brackets) from ``i``; ``css.length``
 * when there is none.
 */
function scanPrelude(css, i) {
  let depth = 0;
  let j = i;
  while (j < css.length) {
    const ch = css[j];
    if (ch === "\\") {
      j += 2;
      continue;
    }
    if (ch === "/" && css[j + 1] === "*") {
      j = skipComment(css, j);
      continue;
    }
    if (ch === '"' || ch === "'") {
      j = skipString(css, j);
      continue;
    }
    if (ch === "(" || ch === "[") depth += 1;
    else if ((ch === ")" || ch === "]") && depth > 0) depth -= 1;
    else if (depth === 0 && (ch === "{" || ch === ";" || ch === "}")) return j;
    j += 1;
  }
  return css.length;
}

/** Index just past the ``}`` matching the ``{`` at ``open``. */
function skipBlock(css, open) {
  let depth = 0;
  let j = open;
  while (j < css.length) {
    const ch = css[j];
    if (ch === "\\") {
      j += 2;
      continue;
    }
    if (ch === "/" && css[j + 1] === "*") {
      j = skipComment(css, j);
      continue;
    }
    if (ch === '"' || ch === "'") {
      j = skipString(css, j);
      continue;
    }
    if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth === 0) return j + 1;
    }
    j += 1;
  }
  return css.length;
}

/** Split a selector list at top-level commas (keeps the pieces verbatim). */
function splitSelectorList(prelude) {
  const parts = [];
  let depth = 0;
  let start = 0;
  let j = 0;
  while (j < prelude.length) {
    const ch = prelude[j];
    if (ch === "\\") {
      j += 2;
      continue;
    }
    if (ch === "/" && prelude[j + 1] === "*") {
      j = skipComment(prelude, j);
      continue;
    }
    if (ch === '"' || ch === "'") {
      j = skipString(prelude, j);
      continue;
    }
    if (ch === "(" || ch === "[") depth += 1;
    else if ((ch === ")" || ch === "]") && depth > 0) depth -= 1;
    else if (ch === "," && depth === 0) {
      parts.push(prelude.slice(start, j));
      start = j + 1;
    }
    j += 1;
  }
  parts.push(prelude.slice(start));
  return parts;
}

/** Length of the first compound selector of ``selector`` (up to a combinator or space). */
function firstCompoundLength(selector) {
  let depth = 0;
  let j = 0;
  while (j < selector.length) {
    const ch = selector[j];
    if (ch === "\\") {
      j += 2;
      continue;
    }
    if (ch === '"' || ch === "'") {
      j = skipString(selector, j);
      continue;
    }
    if (ch === "(" || ch === "[") depth += 1;
    else if ((ch === ")" || ch === "]") && depth > 0) depth -= 1;
    else if (depth === 0 && /[\s>+~]/.test(ch)) return j;
    j += 1;
  }
  return selector.length;
}

const ROOT_COMPOUND = /^(?::root|html|body)(?![\w-])/i;
const THEME_COMPOUND = /^\.dark(?![\w-])/;

function scopeOneSelector(selector, scope) {
  // Keep surrounding whitespace / comments where they were.
  const lead = /^(?:\s|\/\*[\s\S]*?\*\/)*/.exec(selector)[0];
  const trail = /(?:\s|\/\*[\s\S]*?\*\/)*$/.exec(selector.slice(lead.length))[0];
  const core = selector.slice(lead.length, selector.length - trail.length);
  if (!core) return selector;
  // Already scoped (idempotence), or a nesting selector.
  if (core.includes(scope) || core.startsWith("&")) return selector;

  const compoundLength = firstCompoundLength(core);
  const compound = core.slice(0, compoundLength);
  const rest = core.slice(compoundLength);

  let scoped;
  const root = ROOT_COMPOUND.exec(compound);
  if (root && root[0].length === compound.length) {
    // ``:root`` / ``html`` / ``body`` alone → the plugin root.
    scoped = rest ? `${scope}${rest}` : scope;
  } else if (root || THEME_COMPOUND.test(compound)) {
    // ``html.dark``, ``:root[data-x]``, ``.dark``: an ancestor of the plugin root.
    scoped = `${compound} ${scope}${rest}`;
  } else {
    scoped = `${scope} ${core}`;
  }
  return lead + scoped + trail;
}

function scopeSelectorList(prelude, scope) {
  return splitSelectorList(prelude)
    .map((part) => scopeOneSelector(part, scope))
    .join(",");
}

function atRuleName(prelude) {
  const match = /^\s*(?:\/\*[\s\S]*?\*\/\s*)*@([\w-]+)/.exec(prelude);
  return match ? match[1].toLowerCase() : null;
}

/**
 * Prefix every selector of ``css`` with the scope of ``pluginId``. Pure and
 * idempotent: an already scoped selector is left alone.
 */
export function scopeCss(css, pluginId) {
  const scope = scopeSelector(pluginId);
  let out = "";
  let i = 0;
  // Block contexts: "group" (children get scoped) or "rule" (style rule /
  // anything nested in one: copied as written).
  const stack = [];

  while (i < css.length) {
    const stop = scanPrelude(css, i);
    const prelude = css.slice(i, stop);
    if (stop >= css.length) {
      out += prelude;
      break;
    }
    const ch = css[stop];
    if (ch === ";") {
      out += prelude + ";";
      i = stop + 1;
      continue;
    }
    if (ch === "}") {
      out += prelude + "}";
      stack.pop();
      i = stop + 1;
      continue;
    }
    // ch === "{"
    const parent = stack.length ? stack[stack.length - 1] : "group";
    const name = atRuleName(prelude);
    if (name !== null) {
      if (GROUP_AT_RULES.has(name)) {
        out += prelude + "{";
        stack.push(parent === "rule" ? "rule" : "group");
        i = stop + 1;
      } else {
        // @keyframes, @font-face, … and unknown at-rules: verbatim.
        const end = skipBlock(css, stop);
        out += prelude + css.slice(stop, end);
        i = end;
      }
      continue;
    }
    if (parent === "group" && prelude.trim()) {
      out += scopeSelectorList(prelude, scope) + "{";
    } else {
      out += prelude + "{";
    }
    stack.push("rule");
    i = stop + 1;
  }
  return out;
}

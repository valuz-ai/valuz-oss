// Minimal npm-style SemVer: parse, compare, and range inclusion (||, space-
// joined comparators, hyphen ranges, x / * wildcards, ^ and ~), enough for
// ``engines.valuz-plugin-api``. Mirrors cli/internal/pluginpkg/semver.go.

const VERSION_RE =
  /^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/;

export function parseVersion(text) {
  const match = VERSION_RE.exec(String(text).trim());
  if (!match) throw new Error(`invalid version "${text}"`);
  return {
    parts: [Number(match[1]), Number(match[2]), Number(match[3])],
    pre: match[4] ? match[4].split(".") : [],
  };
}

function comparePre(a, b) {
  if (!a.length && !b.length) return 0;
  if (!a.length) return 1;
  if (!b.length) return -1;
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    if (a[i] === undefined) return -1;
    if (b[i] === undefined) return 1;
    const an = /^\d+$/.test(a[i]);
    const bn = /^\d+$/.test(b[i]);
    if (an && bn) {
      const d = Number(a[i]) - Number(b[i]);
      if (d) return Math.sign(d);
    } else if (an !== bn) {
      return an ? -1 : 1;
    } else if (a[i] !== b[i]) {
      return a[i] < b[i] ? -1 : 1;
    }
  }
  return 0;
}

function compareParsed(a, b) {
  for (let i = 0; i < 3; i += 1) {
    if (a.parts[i] !== b.parts[i]) return Math.sign(a.parts[i] - b.parts[i]);
  }
  return comparePre(a.pre, b.pre);
}

/** -1 / 0 / 1 */
export function compareVersions(a, b) {
  return compareParsed(parseVersion(a), parseVersion(b));
}

// A possibly-wildcarded version: n = number of concrete parts.
function parsePartial(text) {
  const t = String(text).trim().replace(/^v/, "").replace(/^=/, "");
  if (t === "" || t === "*" || /^[xX]$/.test(t)) return { parts: [0, 0, 0], n: 0, pre: [] };
  const [core, ...preParts] = t.split("+")[0].split("-");
  const pre = preParts.length ? preParts.join("-").split(".") : [];
  const fields = core.split(".");
  if (fields.length > 3) throw new Error(`invalid version "${text}"`);
  const parts = [0, 0, 0];
  let n = 0;
  for (const field of fields) {
    if (field === "*" || /^[xX]$/.test(field)) break;
    if (!/^(0|[1-9]\d*)$/.test(field)) throw new Error(`invalid version "${text}"`);
    parts[n] = Number(field);
    n += 1;
  }
  return { parts, n, pre: n === 3 ? pre : [] };
}

const floor = (p) => ({ parts: [...p.parts], pre: p.pre });
function bump(p) {
  // The smallest version above every version matched by the partial.
  const parts = [...p.parts];
  if (p.n === 0) return null;
  parts[p.n - 1] += 1;
  for (let i = p.n; i < 3; i += 1) parts[i] = 0;
  return { parts, pre: ["0"] };
}

function parseComparator(token) {
  const match = /^(<=|>=|<|>|=|\^|~>|~)?\s*(.*)$/.exec(token);
  const op = match[1] ?? "";
  const p = parsePartial(match[2]);
  const out = [];
  const lower = (v) => out.push({ op: ">=", v });
  const upper = (v) => v && out.push({ op: "<", v });
  switch (op) {
    case "^": {
      if (p.n === 0) return [];
      lower(floor(p));
      const [major, minor, patch] = p.parts;
      if (major > 0 || p.n === 1) upper({ parts: [major + 1, 0, 0], pre: ["0"] });
      else if (minor > 0 || p.n === 2) upper({ parts: [0, minor + 1, 0], pre: ["0"] });
      else upper({ parts: [0, 0, patch + 1], pre: ["0"] });
      return out;
    }
    case "~":
    case "~>": {
      if (p.n === 0) return [];
      lower(floor(p));
      upper(bump({ ...p, n: Math.min(p.n, 2) }));
      return out;
    }
    case ">":
      if (p.n === 0) return [{ op: "<", v: { parts: [0, 0, 0], pre: ["0"] } }];
      return p.n === 3 ? [{ op: ">", v: floor(p) }] : [{ op: ">=", v: bump(p) }];
    case ">=":
      return p.n === 0 ? [] : [{ op: ">=", v: floor(p) }];
    case "<":
      return p.n === 0 ? [{ op: "<", v: { parts: [0, 0, 0], pre: ["0"] } }] : [{ op: "<", v: { ...floor(p), pre: p.n === 3 ? p.pre : ["0"] } }];
    case "<=":
      if (p.n === 0) return [];
      return p.n === 3 ? [{ op: "<=", v: floor(p) }] : [{ op: "<", v: bump(p) }];
    default:
      if (p.n === 0) return [];
      if (p.n === 3) return [{ op: "=", v: floor(p) }];
      lower(floor(p));
      upper(bump(p));
      return out;
  }
}

function parseComparatorSet(set) {
  const fields = set.trim().split(/\s+/).filter(Boolean);
  if (fields.length === 3 && fields[1] === "-") {
    const lo = parsePartial(fields[0]);
    const hi = parsePartial(fields[2]);
    const out = [];
    if (lo.n > 0) out.push({ op: ">=", v: floor(lo) });
    if (hi.n === 3) out.push({ op: "<=", v: floor(hi) });
    else if (hi.n > 0) out.push({ op: "<", v: bump(hi) });
    return out;
  }
  const tokens = [];
  for (let i = 0; i < fields.length; i += 1) {
    let field = fields[i];
    if (/^(<=|>=|<|>|=|\^|~>|~)$/.test(field) && i + 1 < fields.length) {
      field += fields[i + 1];
      i += 1;
    }
    tokens.push(field);
  }
  return tokens.flatMap(parseComparator);
}

function test(comparator, version) {
  const d = compareParsed(version, comparator.v);
  switch (comparator.op) {
    case ">":
      return d > 0;
    case ">=":
      return d >= 0;
    case "<":
      return d < 0;
    case "<=":
      return d <= 0;
    default:
      return d === 0;
  }
}

/** Whether npm-style ``range`` includes ``version`` (throws on a malformed range). */
export function rangeIncludes(range, version) {
  const v = parseVersion(version);
  return String(range)
    .split("||")
    .some((set) => parseComparatorSet(set).every((c) => test(c, v)));
}

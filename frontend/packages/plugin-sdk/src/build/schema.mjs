// A small JSON Schema evaluator for the subset valuz-plugin.schema.json uses:
// $ref (local), type, const, enum, required, properties, additionalProperties,
// propertyNames, minProperties, items, minItems, maxItems, uniqueItems,
// minLength, maxLength, pattern, minimum, maximum, oneOf, anyOf, allOf.
// Annotations (description, default, title, x-*) are ignored. Messages match
// the Go CLI's validator (cli/internal/pluginpkg/schema.go).

function compactJson(value) {
  return JSON.stringify(value);
}

function childPath(base, key) {
  return base ? `${base}.${key}` : key;
}

function indexPath(base, index) {
  return `${base}[${index}]`;
}

function typeOf(value) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  if (typeof value === "number") return Number.isInteger(value) ? "integer" : "number";
  return typeof value;
}

function typeMatches(type, value) {
  const actual = typeOf(value);
  if (type === "number") return actual === "number" || actual === "integer";
  return actual === type;
}

function article(type) {
  return /^[aeiou]/.test(type) ? `an ${type}` : `a ${type}`;
}

function joinOr(items) {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} or ${items[items.length - 1]}`;
}

function deepEqual(a, b) {
  return compactJson(normalize(a)) === compactJson(normalize(b));
}

function normalize(value) {
  if (Array.isArray(value)) return value.map(normalize);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, normalize(value[key])]),
    );
  }
  return value;
}

/** Validate ``value`` against ``root`` (a schema); returns ``"<path>: <message>"`` strings. */
export function validateSchema(root, value) {
  const regexps = new Map();
  const regexp = (pattern) => {
    if (!regexps.has(pattern)) regexps.set(pattern, new RegExp(pattern, "u"));
    return regexps.get(pattern);
  };
  const resolve = (ref) => {
    if (!ref.startsWith("#/")) throw new Error(`unsupported $ref "${ref}"`);
    let current = root;
    for (const part of ref.slice(2).split("/")) {
      current = current?.[part];
    }
    if (!current || typeof current !== "object") throw new Error(`unresolvable $ref "${ref}"`);
    return current;
  };

  const evaluate = (schema, v, at) => {
    const out = [];
    if (typeof schema !== "object" || schema === null) return out;
    if (typeof schema.$ref === "string") {
      try {
        out.push(...evaluate(resolve(schema.$ref), v, at));
      } catch (error) {
        out.push({ path: at, message: error.message });
      }
    }
    if (typeof schema.type === "string" && !typeMatches(schema.type, v)) {
      out.push({ path: at, message: `must be ${article(schema.type)}` });
      return out;
    }
    if (Array.isArray(schema.type) && !schema.type.some((t) => typeMatches(t, v))) {
      out.push({ path: at, message: `must be ${joinOr(schema.type.map(article))}` });
      return out;
    }
    if ("const" in schema && !deepEqual(schema.const, v)) {
      out.push({ path: at, message: `must be ${compactJson(schema.const)}` });
    }
    if (Array.isArray(schema.enum) && !schema.enum.some((e) => deepEqual(e, v))) {
      out.push({
        path: at,
        message: `${compactJson(v)} is not one of: ${schema.enum.map(compactJson).join(", ")}`,
      });
    }
    if (Array.isArray(schema.oneOf)) out.push(...evaluateOneOf(schema.oneOf, v, at));
    if (Array.isArray(schema.anyOf)) {
      const results = schema.anyOf.map((branch) => evaluate(branch, v, at));
      if (!results.some((r) => r.length === 0)) {
        out.push(...results.reduce((best, r) => (r.length < best.length ? r : best)));
      }
    }
    if (Array.isArray(schema.allOf)) {
      for (const branch of schema.allOf) out.push(...evaluate(branch, v, at));
    }
    const kind = typeOf(v);
    if (kind === "object") out.push(...evaluateObject(schema, v, at));
    else if (kind === "array") out.push(...evaluateArray(schema, v, at));
    else if (kind === "string") out.push(...evaluateString(schema, v, at));
    else if (kind === "number" || kind === "integer") out.push(...evaluateNumber(schema, v, at));
    return out;
  };

  const evaluateObject = (schema, obj, at) => {
    const out = [];
    for (const name of Array.isArray(schema.required) ? schema.required : []) {
      if (!(name in obj)) out.push({ path: childPath(at, name), message: "is required" });
    }
    const props = schema.properties && typeof schema.properties === "object" ? schema.properties : {};
    for (const key of Object.keys(obj).sort()) {
      if (Object.prototype.hasOwnProperty.call(props, key)) {
        out.push(...evaluate(props[key], obj[key], childPath(at, key)));
        continue;
      }
      const extra = schema.additionalProperties;
      if (extra === false) {
        out.push({ path: childPath(at, key), message: "is not a known field" });
      } else if (extra && typeof extra === "object") {
        out.push(...evaluate(extra, obj[key], childPath(at, key)));
      }
    }
    if (schema.propertyNames && typeof schema.propertyNames === "object") {
      for (const key of Object.keys(obj).sort()) {
        for (const issue of evaluate(schema.propertyNames, key, "")) {
          out.push({ path: at, message: `key ${compactJson(key)}: ${issue.message}` });
        }
      }
    }
    if (Number.isInteger(schema.minProperties) && Object.keys(obj).length < schema.minProperties) {
      const n = schema.minProperties;
      out.push({ path: at, message: `must have at least ${n} entr${n === 1 ? "y" : "ies"}` });
    }
    return out;
  };

  const evaluateArray = (schema, arr, at) => {
    const out = [];
    if (Number.isInteger(schema.minItems) && arr.length < schema.minItems) {
      const n = schema.minItems;
      out.push({ path: at, message: `must have at least ${n} item${n === 1 ? "" : "s"}` });
    }
    if (Number.isInteger(schema.maxItems) && arr.length > schema.maxItems) {
      const n = schema.maxItems;
      out.push({ path: at, message: `must have at most ${n} item${n === 1 ? "" : "s"}` });
    }
    if (schema.uniqueItems === true) {
      const seen = new Set();
      for (const item of arr) {
        const key = compactJson(normalize(item));
        if (seen.has(key)) {
          out.push({ path: at, message: `must not contain duplicates (${compactJson(item)})` });
          continue;
        }
        seen.add(key);
      }
    }
    if (schema.items && typeof schema.items === "object" && !Array.isArray(schema.items)) {
      arr.forEach((item, i) => out.push(...evaluate(schema.items, item, indexPath(at, i))));
    }
    return out;
  };

  const evaluateString = (schema, str, at) => {
    const out = [];
    const length = [...str].length;
    if (Number.isInteger(schema.minLength) && length < schema.minLength) {
      out.push({
        path: at,
        message: schema.minLength === 1 ? "must not be empty" : `must be at least ${schema.minLength} characters`,
      });
    }
    if (Number.isInteger(schema.maxLength) && length > schema.maxLength) {
      out.push({ path: at, message: `must be at most ${schema.maxLength} characters` });
    }
    if (typeof schema.pattern === "string") {
      let re = null;
      try {
        re = regexp(schema.pattern);
      } catch (error) {
        out.push({ path: at, message: `schema pattern ${compactJson(schema.pattern)} does not compile: ${error.message}` });
      }
      if (re && !re.test(str)) {
        let message = `${compactJson(str)} does not match ${schema.pattern}`;
        if (typeof schema.description === "string" && schema.description) {
          message += ` (${schema.description})`;
        }
        out.push({ path: at, message });
      }
    }
    return out;
  };

  const evaluateNumber = (schema, num, at) => {
    const out = [];
    if (typeof schema.minimum === "number" && num < schema.minimum) {
      out.push({ path: at, message: `must be at least ${schema.minimum}` });
    }
    if (typeof schema.maximum === "number" && num > schema.maximum) {
      out.push({ path: at, message: `must be at most ${schema.maximum}` });
    }
    return out;
  };

  // oneOf: exactly one branch passes. On failure report the closest branch
  // (same declared type, fewest findings) so the message says what to fix.
  const evaluateOneOf = (branches, v, at) => {
    const results = branches.map((schema) => ({ schema, issues: evaluate(schema, v, at) }));
    const passed = results.filter((r) => r.issues.length === 0).length;
    if (passed === 1) return [];
    if (passed > 1) return [{ path: at, message: "matches more than one allowed form" }];
    for (const wantTyped of [true, false]) {
      let best = null;
      for (const r of results) {
        const typed = typeof r.schema?.type === "string";
        if (wantTyped && (!typed || !typeMatches(r.schema.type, v))) continue;
        if (!wantTyped && typed) continue;
        if (!best || r.issues.length < best.issues.length) best = r;
      }
      if (best) return best.issues;
    }
    const forms = [];
    for (const r of results) {
      let form = "an allowed value";
      if (typeof r.schema?.type === "string") form = article(r.schema.type);
      else if (r.schema && "const" in r.schema) form = compactJson(r.schema.const);
      if (!forms.includes(form)) forms.push(form);
    }
    return [{ path: at, message: `must be ${joinOr(forms)}` }];
  };

  return evaluate(root, value, "").map((issue) =>
    issue.path ? `${issue.path}: ${issue.message}` : issue.message,
  );
}

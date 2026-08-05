import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const stylesDir = join(dirname(fileURLToPath(import.meta.url)), "styles");

function readAll(): { file: string; css: string }[] {
  return readdirSync(stylesDir)
    .filter((f) => f.endsWith(".css"))
    .map((file) => ({ file, css: readFileSync(join(stylesDir, file), "utf8") }));
}

/** Rule bodies keyed by selector, split into base rules and container-query rules. */
function splitRules(css: string) {
  const base: { selector: string; body: string }[] = [];
  const narrow: { selector: string; body: string }[] = [];
  // Strip comments first so `/* min-width: 320px */` never counts as a rule.
  const clean = css.replace(/\/\*[\s\S]*?\*\//g, "");

  // Container blocks are the only nesting these files use, so a non-greedy
  // match up to the closing brace of the at-rule is enough.
  const containerRe = /@container[^{]*\(max-width:\s*30rem\)\s*\{([\s\S]*?)\n\}/g;
  let m: RegExpExecArray | null;
  const containerBodies: string[] = [];
  while ((m = containerRe.exec(clean)) !== null) containerBodies.push(m[1] ?? "");

  const ruleRe = /([^{}@]+)\{([^{}]*)\}/g;
  const collect = (source: string, into: { selector: string; body: string }[]) => {
    let r: RegExpExecArray | null;
    while ((r = ruleRe.exec(source)) !== null) {
      into.push({ selector: (r[1] ?? "").trim().replace(/\s+/g, " "), body: r[2] ?? "" });
    }
  };
  collect(clean.replace(containerRe, ""), base);
  for (const body of containerBodies) collect(body, narrow);
  return { base, narrow };
}

// A floor written as `min(320px, 100%)` already concedes to a narrower
// container, so it is not the hazard this guards. A bare floor is.
const NON_ZERO_MIN_WIDTH = /min-width:\s*(?!0\b|min\()([0-9.]+)(px|rem|em)/;
const SETS_BASIS = /flex(-basis)?:\s*[^;]*\b([0-9.]+(px|rem|em)|100%)/;

describe("block stylesheets", () => {
  it("relaxes every wrap-grid min-width floor at the narrowest breakpoint", () => {
    // The failure this guards is silent and easy to reintroduce: a floor makes
    // a card hold its shape, but a floor left in place when the column gets
    // narrower than the floor makes the card overflow its own container
    // instead of shrinking — so the whole chat column scrolls sideways. Any
    // selector that both claims a width (flex-basis) and sets a bare floor has
    // to give it up at 30rem. Writing the floor as `min(…, 100%)` is the better
    // answer and is accepted instead: a floor that exceeds its container does
    // not shrink the container, it overflows it and paints over the neighbour.
    const offenders: string[] = [];
    for (const { file, css } of readAll()) {
      const { base, narrow } = splitRules(css);
      const relaxed = new Set(
        narrow
          .filter((r) => /min-width:\s*0\b/.test(r.body))
          .flatMap((r) => r.selector.split(",").map((s) => s.trim())),
      );
      for (const rule of base) {
        if (!NON_ZERO_MIN_WIDTH.test(rule.body) || !SETS_BASIS.test(rule.body)) continue;
        for (const selector of rule.selector.split(",").map((s) => s.trim())) {
          if (!relaxed.has(selector)) offenders.push(`${file}: ${selector}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("has a container to resolve its container queries against", () => {
    // `@container vgb (...)` only matches inside an element carrying
    // `.vgb-root`. Miss that and every breakpoint silently never fires — a tile
    // keeps its widest floor at every width and overflows its column, painting
    // over whatever sits beside it. Nothing errors; the layout is just wrong.
    const hosts = resolve(stylesDir, "../../../ui/src/components/conversation");
    const wired = readdirSync(hosts)
      .filter((f) => f.endsWith(".tsx") && !f.includes(".test."))
      .some((f) => readFileSync(join(hosts, f), "utf8").includes('className="vgb-root"'));
    expect(wired, "no component establishes the vgb-root container").toBe(true);

    // And the rules that depend on it must actually exist, or the check above
    // would pass on a package that had quietly stopped using container queries.
    const queries = readAll().reduce(
      (n, { css }) => n + (css.match(/@container vgb /g) ?? []).length,
      0,
    );
    expect(queries).toBeGreaterThan(3);
  });

  it("uses container queries rather than viewport media queries for layout", () => {
    // Blocks render inside a chat column whose width has nothing to do with the
    // viewport's. The only legitimate @media here is prefers-reduced-motion and
    // print, both of which are about the user or the output device.
    const offenders: string[] = [];
    for (const { file, css } of readAll()) {
      for (const m of css.matchAll(/@media([^{]*)\{/g)) {
        const query = (m[1] ?? "").trim();
        if (/prefers-reduced-motion|print|prefers-color-scheme|forced-colors/.test(query)) continue;
        offenders.push(`${file}: @media ${query}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("takes every colour from an --openui-* token", () => {
    // A literal colour ignores the host theme, and the whole point of the
    // token indirection is that a block restyles itself when the theme changes.
    const offenders: string[] = [];
    for (const { file, css } of readAll()) {
      const clean = css.replace(/\/\*[\s\S]*?\*\//g, "");
      for (const m of clean.matchAll(/(#[0-9a-fA-F]{3,8}\b|\brgba?\([^)]*\)|\bhsla?\([^)]*\))/g)) {
        offenders.push(`${file}: ${m[1]}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});

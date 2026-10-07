import { describe, expect, it } from "vitest";

import { scopeCss, scopeSelector } from "./css-scope.mjs";

const S = '[data-valuz-app-plugin="acme.x"]';

describe("scopeCss", () => {
  it("prefixes every selector of a list", () => {
    expect(scopeCss(".a, .b > c { color: red }", "acme.x")).toBe(
      `${S} .a, ${S} .b > c { color: red }`,
    );
    expect(scopeCss(".a,\n.b{x:y}", "acme.x")).toBe(`${S} .a,\n${S} .b{x:y}`);
  });

  it("maps :root / html / body to the scope and keeps theme ancestors in front", () => {
    expect(scopeCss(":root { --c: red }", "acme.x")).toBe(`${S} { --c: red }`);
    expect(scopeCss("html .a {}", "acme.x")).toBe(`${S} .a {}`);
    expect(scopeCss("body{}", "acme.x")).toBe(`${S}{}`);
    expect(scopeCss(".dark .a {}", "acme.x")).toBe(`.dark ${S} .a {}`);
    expect(scopeCss("html.dark .a {}", "acme.x")).toBe(`html.dark ${S} .a {}`);
    expect(scopeCss(":root.dark { --c: blue }", "acme.x")).toBe(
      `:root.dark ${S} { --c: blue }`,
    );
  });

  it("scopes inside @media / @supports / @layer but not nested rules", () => {
    const css =
      "@media (min-width: 600px) { .a { color: red } @supports (display: grid) { .b {} } }";
    expect(scopeCss(css, "acme.x")).toBe(
      `@media (min-width: 600px) { ${S} .a { color: red } @supports (display: grid) { ${S} .b {} } }`,
    );
    expect(
      scopeCss(
        ".card { color: red; .title { x: y } &:hover { z: w } }",
        "acme.x",
      ),
    ).toBe(`${S} .card { color: red; .title { x: y } &:hover { z: w } }`);
    expect(scopeCss("@layer base { .a {} }", "acme.x")).toBe(
      `@layer base { ${S} .a {} }`,
    );
  });

  it("copies @keyframes, @font-face and statements verbatim", () => {
    const keyframes =
      "@keyframes spin { from { transform: rotate(0) } 50% { opacity: .5 } to { transform: rotate(1turn) } }";
    expect(scopeCss(keyframes, "acme.x")).toBe(keyframes);
    const font = '@font-face { font-family: "X"; src: url("x.woff2") }';
    expect(scopeCss(font, "acme.x")).toBe(font);
    expect(
      scopeCss(
        '@import url("a.css");\n@charset "utf-8";\n@layer a, b;',
        "acme.x",
      ),
    ).toBe('@import url("a.css");\n@charset "utf-8";\n@layer a, b;');
  });

  it("is not fooled by strings, comments, attribute selectors and url()", () => {
    expect(
      scopeCss('a[title="x,{y}"], .b::after { content: "}" }', "acme.x"),
    ).toBe(`${S} a[title="x,{y}"], ${S} .b::after { content: "}" }`);
    expect(
      scopeCss(
        "/* .c { } */ .d { background: url(data:image/png;base64,AAA=) }",
        "acme.x",
      ),
    ).toBe(
      `/* .c { } */ ${S} .d { background: url(data:image/png;base64,AAA=) }`,
    );
    expect(scopeCss(":is(.a, .b) .c {}", "acme.x")).toBe(
      `${S} :is(.a, .b) .c {}`,
    );
  });

  it("is idempotent", () => {
    const once = scopeCss(
      ".a, :root, .dark .b {} @media print { .c {} }",
      "acme.x",
    );
    expect(scopeCss(once, "acme.x")).toBe(once);
  });

  it("builds the scope selector", () => {
    expect(scopeSelector("acme.x")).toBe(S);
  });
});

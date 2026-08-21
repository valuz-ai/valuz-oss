import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const sourceRoot = resolve(__dirname, "..");

describe("standalone A2UI CSS theme", () => {
  it("declares stable cascade layers", () => {
    const stylesheet = readFileSync(resolve(sourceRoot, "styles.css"), "utf8");
    expect(stylesheet).toContain(
      "@layer a2ui.structure, a2ui.theme, a2ui.components, a2ui.distribution, a2ui.surface;",
    );
    expect(stylesheet).toContain('layer(a2ui.theme)');
    expect(stylesheet).toContain('layer(a2ui.components)');
  });

  it("does not inherit application theme variables or a host dark class", () => {
    const theme = readFileSync(resolve(sourceRoot, "themes/default.css"), "utf8");
    expect(theme).not.toMatch(/var\(--(?:background|surface|foreground|brand|fg-|accent-)/);
    expect(theme).not.toContain(".dark .valuz-a2ui");
  });

  it("declares the curated C1 palette colors as stable theme tokens", () => {
    const theme = readFileSync(resolve(sourceRoot, "themes/default.css"), "utf8");
    expect(theme).toContain("--va2-chart-ocean-6: #42a5f5;");
    expect(theme).toContain("--va2-chart-orchid-6: #883bd5;");
    expect(theme).toContain("--va2-chart-vivid-6: #36949d;");
    expect(theme).toContain("--va2-chart-steel-6: #687b8f;");
    expect(theme).toContain("--va2-chart-amber-6: #d88700;");
    expect(theme).toContain("--va2-chart-actual: var(--va2-chart-ocean-6);");
  });

  it("anchors visually hidden form controls inside their labels", () => {
    const forms = readFileSync(resolve(sourceRoot, "styles/forms.css"), "utf8");
    expect(forms).toMatch(/\.va2-choice \{[^}]*position: relative;/);
    expect(forms).toMatch(/\.va2-choice > input \{[^}]*left: 0;[^}]*position: absolute;[^}]*top: 0;/);
    expect(forms).toMatch(/\.va2-switch \{[^}]*position: relative;/);
    expect(forms).toMatch(/\.va2-switch input \{[^}]*left: 0;[^}]*position: absolute;[^}]*top: 0;/);
  });
});

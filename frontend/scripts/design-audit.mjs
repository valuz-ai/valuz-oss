#!/usr/bin/env node

import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const baselinePath = resolve(root, "scripts/design-audit-baseline.json");
const update = process.argv.includes("--update-baseline");

const rules = [
  {
    id: "hex-color",
    description: "Hardcoded hex colors outside token files",
    pattern: /#[0-9a-fA-F]{3,8}\b/g,
  },
  {
    id: "arbitrary-px-text",
    description: "Arbitrary pixel font sizes, e.g. text-[12.5px]",
    pattern: /\b(?:before:|after:)?text-\[[0-9.]+px\]/g,
  },
  {
    id: "arbitrary-px-radius",
    description: "Arbitrary pixel radius, e.g. rounded-[7px]",
    pattern: /\b(?:rounded|rounded-[trbl]{1,2})-\[[0-9.]+px\]/g,
  },
  {
    id: "manual-rgba-shadow",
    description: "Hand-written rgba shadow utilities instead of shadow tokens",
    pattern: /\bshadow-\[[^\]\n]*(?:rgba|rgb)\([^\]\n]+\)\]/g,
  },
  {
    id: "tailwind-palette-color",
    description: "Tailwind palette color utilities instead of semantic tokens",
    pattern:
      /\b(?:hover:|active:|focus:|focus-visible:|disabled:|dark:|data-\[[^\]]+\]:|group-hover:|aria-\[[^\]]+\]:)*(?:bg|text|border|ring|from|to|via|fill|stroke)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}(?:\/[0-9]{1,3})?\b/g,
  },
  {
    id: "secondary-variant",
    description: 'Deprecated secondary component variant',
    pattern: /\bvariant=["']secondary["']/g,
  },
];

const scanRoots = ["apps", "packages", "src"];
const files = scanRoots.flatMap((dir) => collectFiles(resolve(root, dir)));

const counts = Object.fromEntries(rules.map((rule) => [rule.id, 0]));
const examples = Object.fromEntries(rules.map((rule) => [rule.id, []]));

for (const file of files) {
  const rel = relative(root, file);
  const content = readFileSync(file, "utf8");
  const lines = content.split(/\r?\n/);
  for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
    const line = lines[lineIndex];
    for (const rule of rules) {
      const matches = line.match(rule.pattern);
      if (!matches) continue;
      counts[rule.id] += matches.length;
      if (examples[rule.id].length < 8) {
        examples[rule.id].push({
          file: rel,
          line: lineIndex + 1,
          match: matches[0],
        });
      }
    }
  }
}

if (update) {
  writeFileSync(
    baselinePath,
    `${JSON.stringify(
      {
        version: 1,
        note:
          "Current known design-rule debt. scripts/design-audit.mjs fails when counts increase.",
        generatedAt: new Date().toISOString(),
        counts,
      },
      null,
      2,
    )}\n`,
  );
  console.log(`Updated ${relative(root, baselinePath)}`);
  printSummary(counts);
  process.exit(0);
}

if (!existsSync(baselinePath)) {
  console.error(
    `Missing ${relative(root, baselinePath)}. Run: pnpm design-audit:update`,
  );
  process.exit(1);
}

const baseline = JSON.parse(readFileSync(baselinePath, "utf8"));
const baselineCounts = baseline.counts ?? {};
const failures = [];

for (const rule of rules) {
  const current = counts[rule.id] ?? 0;
  const allowed = baselineCounts[rule.id] ?? 0;
  if (current > allowed) {
    failures.push({ rule, current, allowed });
  }
}

printSummary(counts, baselineCounts);

if (failures.length > 0) {
  console.error("\nDesign audit failed: violation counts increased.");
  for (const { rule, current, allowed } of failures) {
    console.error(`\n${rule.id}: ${current} > baseline ${allowed}`);
    console.error(`  ${rule.description}`);
    for (const ex of examples[rule.id]) {
      console.error(`  - ${ex.file}:${ex.line} ${ex.match}`);
    }
  }
  console.error("\nFix the new violation, or intentionally refresh the baseline.");
  process.exit(1);
}

function printSummary(current, baseline) {
  const rows = rules.map((rule) => {
    const base = baseline ? baseline[rule.id] ?? 0 : "-";
    return `${rule.id.padEnd(24)} current=${String(current[rule.id]).padStart(
      4,
    )} baseline=${String(base).padStart(4)}`;
  });
  console.log(["Design audit summary:", ...rows].join("\n"));
}

function collectFiles(dir) {
  if (!existsSync(dir)) return [];
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = resolve(dir, entry);
    const rel = relative(root, full);
    if (shouldSkipPath(rel)) continue;
    const st = statSync(full);
    if (st.isDirectory()) {
      out.push(...collectFiles(full));
    } else if (/\.(ts|tsx|css)$/.test(entry) && !/\.(test|spec)\.(ts|tsx)$/.test(entry)) {
      out.push(full);
    }
  }
  return out;
}

function shouldSkipPath(rel) {
  return (
    rel.includes("/node_modules/") ||
    rel.includes("/dist/") ||
    (rel.includes("/dist-demo/") || rel.endsWith("/dist-demo")) ||
    rel.includes("/.turbo/") ||
    rel.startsWith("apps/desktop/src/renderer/assets/") ||
    rel === "packages/ui/src/styles/project.css" ||
    rel === "packages/a2ui/src/styles/base.css"
  );
}

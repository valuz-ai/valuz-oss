#!/usr/bin/env node
/**
 * Export the dsh web client's SlotMap catalog to a JSON fixture.
 *
 * dsh ships the catalog of every UI slot its web bundle declares as the
 * literal `CLIENT_SLOT_API` inside `@deepseek-ai/dsh-cordis-client-runner`'s
 * browser bundle (generated upstream from the SlotMap declarations). The
 * fixture is what Valuz's dsh-slot mapping is checked against
 * (frontend/packages/core/src/edition/dsh-slot-map.test.ts), so a slot added,
 * renamed or removed upstream fails a test instead of silently not rendering.
 *
 *   node scripts/dsh-slot-catalog.mjs <client.js> <out.json> <dsh-version>
 */
import { readFileSync, writeFileSync } from "node:fs";

const [clientPath, outPath, version] = process.argv.slice(2);
if (!clientPath || !outPath || !version) {
  console.error("usage: dsh-slot-catalog.mjs <client.js> <out.json> <dsh-version>");
  process.exit(2);
}

const source = readFileSync(clientPath, "utf8");
const marker = "const CLIENT_SLOT_API = [";
const start = source.indexOf(marker);
if (start === -1) {
  console.error(`no CLIENT_SLOT_API in ${clientPath} — the upstream catalog moved`);
  process.exit(1);
}

// Bracket-match the array literal, skipping string and template contents.
let index = start + marker.length - 1;
let depth = 0;
let quote = null;
for (; index < source.length; index++) {
  const ch = source[index];
  if (quote) {
    if (ch === "\\") index++;
    else if (ch === quote) quote = null;
    continue;
  }
  if (ch === '"' || ch === "'" || ch === "`") quote = ch;
  else if (ch === "[") depth++;
  else if (ch === "]" && --depth === 0) break;
}
const literal = source.slice(start + marker.length - 1, index + 1);
const catalog = new Function(`return ${literal};`)();

const slots = catalog
  .map((entry) => ({
    key: entry.key,
    kind: entry.kind,
    scope: entry.scope,
    summary: entry.summary ?? "",
  }))
  .sort((a, b) => a.key.localeCompare(b.key));

writeFileSync(outPath, `${JSON.stringify({ dsh: version, slots }, null, 2)}\n`);
console.log(`dsh slot catalog: ${slots.length} slots (dsh ${version}) -> ${outPath}`);

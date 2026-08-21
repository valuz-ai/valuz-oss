import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  renderA2UIComponentCatalogText,
  valuzBaseComponentApis,
} from "@valuz/a2ui/catalog";

const here = dirname(fileURLToPath(import.meta.url));
const target = resolve(
  here,
  "../../../../backend/valuz_agent/modules/genui/a2ui_component_catalog.txt",
);
mkdirSync(dirname(target), { recursive: true });
writeFileSync(target, `${renderA2UIComponentCatalogText(valuzBaseComponentApis)}\n`, "utf8");
console.log(`wrote ${target}`);

// Regenerates the vendored generative-UI prompt assets. Run after bumping
// @openuidev/react-ui or after adding/changing a block in @valuz/genui-blocks.
// Both outputs are loaded by the generate_ui tool at runtime
// (backend/valuz_agent/modules/genui/). Dev-only — not imported by the app.
//
//   pnpm --filter @valuz/ui gen:openui-prompt
//
// Two protocols, two assets, one source:
//
//   openui_genui_lib_prompt.txt  — the OpenUI Lang system prompt, generated
//     from the MERGED library (OpenUI's components plus the blocks), because
//     the renderer resolves against that same merged library at runtime.
//   a2ui_block_catalog.txt       — the block section of the A2UI catalog,
//     generated from the same block registry that A2UIRenderer builds its
//     component list from.
//
// Generating either by hand is the silent failure mode here: a block would
// still render if the model emitted it, but nothing would have told the model
// it exists — or worse, the model is told about a component that was removed.
import { createValuzLibrary, renderBlockCatalogText, valuzPromptOptions } from "@valuz/genui-blocks";
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const genuiDir = resolve(here, "../../../../backend/valuz_agent/modules/genui");

const write = (name, contents) => {
  const path = resolve(genuiDir, name);
  writeFileSync(path, contents);
  console.log(`wrote ${path} (${contents.length} chars)`);
};

write("openui_genui_lib_prompt.txt", createValuzLibrary().prompt(valuzPromptOptions));
write("a2ui_block_catalog.txt", `${renderBlockCatalogText()}\n`);

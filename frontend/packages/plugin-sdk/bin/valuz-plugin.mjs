#!/usr/bin/env node
// valuz-plugin — the @valuz/plugin-sdk command line: create, build, dev, test,
// validate and pack third-party Valuz plugins (task card 04, doc 12 §7).
//
// Plain Node ESM, no build step. esbuild and React are resolved from the SDK's
// own dependencies (createRequire relative to the SDK, not the working
// directory), so the command works on a plugin directory anywhere on disk,
// including one without node_modules (an agent session's workspace,
// /tmp/my-plugin, …). The build logic is the JS core of
// ``@valuz/plugin-sdk/build`` (src/build/*.mjs); the commands are in lib/cli.mjs.
import process from "node:process";

import { main } from "./lib/cli.mjs";

main(process.argv.slice(2)).then(
  (code) => process.exit(code ?? 0),
  (error) => {
    process.stderr.write(`${error?.stack ?? error}\n`);
    process.exit(1);
  },
);

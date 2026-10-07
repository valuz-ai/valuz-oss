// Generate stable session receipts and automation target contracts.
import { mkdtempSync, rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repo = path.resolve(frontend, "..");
const temp = mkdtempSync(path.join(tmpdir(), "valuz-session-contract-"));
function run(command, args, cwd) {
  const result = spawnSync(command, args, { cwd, stdio: "inherit" });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} exited ${result.status}`);
}
try {
  const contract = path.join(temp, "contract.json");
  run("uv", ["run", "--project", "backend", "python", "-c", `
import json,sys,yaml
from pathlib import Path
spec=yaml.safe_load(Path('api/openapi.yaml').read_text())
parts={name:spec['components']['schemas'][name] for name in ('SessionInputReceipt','AutomationAgentExecution')}
Path(sys.argv[1]).write_text(json.dumps({'openapi':spec['openapi'],'info':spec['info'],'paths':{},'components':{'schemas':parts}}))
`, contract], repo);
  run("npm", ["exec", "--yes", "--package=openapi-typescript@7.12.0", "--", "openapi-typescript", contract,
    "-o", "packages/core/src/api/generated/sessions.ts"], frontend);
} finally { rmSync(temp, { recursive: true, force: true }); }

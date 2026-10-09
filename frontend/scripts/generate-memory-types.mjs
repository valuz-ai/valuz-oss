#!/usr/bin/env node
import { mkdtempSync, rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repo = path.resolve(frontend, "..");
const temporary = mkdtempSync(path.join(tmpdir(), "valuz-memory-contract-"));
function run(command, args, cwd) {
  const result = spawnSync(command, args, {cwd, stdio: "inherit"});
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} exited ${result.status}`);
}
try {
  const contract = path.join(temporary, "memory.json");
  run("uv", ["run", "--project", "backend", "python", "-c", `
import json, re, sys, yaml
from pathlib import Path
spec=yaml.safe_load(Path('api/openapi.yaml').read_text())
paths={key:value for key,value in spec['paths'].items() if key.startswith('/v1/memory') or key == '/v1/projects/import/confirm'}
refs=set(re.findall(r'#/components/[^"\\ ]+',json.dumps(paths)))
parts={}
while refs:
    ref=refs.pop()
    _,_,kind,name=ref.split('/')
    group=parts.setdefault(kind,{})
    if name in group: continue
    value=spec['components'][kind][name]
    group[name]=value
    refs.update(re.findall(r'#/components/[^"\\ ]+',json.dumps(value)))
output={key:spec[key] for key in ('openapi','info')}
output.update(paths=paths,components=parts)
Path(sys.argv[1]).write_text(json.dumps(output))
`, contract], repo);
  run("npm", ["exec", "--yes", "--package=openapi-typescript@7.12.0", "--", "openapi-typescript", contract, "--default-non-nullable", "false", "-o", "packages/core/src/api/generated/memory.ts"], frontend);
} finally { rmSync(temporary, {recursive: true, force: true}); }

#!/usr/bin/env node
// Generate the search contract from the actual FastAPI/Pydantic route.
import { mkdtempSync, rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repo = path.resolve(frontend, "..");
const temp = mkdtempSync(path.join(tmpdir(), "valuz-docs-contract-"));
const contract = path.join(temp, "contract.json");
function run(command, args, cwd) {
  const result = spawnSync(command, args, { cwd, stdio: "inherit" });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} exited ${result.status}`);
}
try {
  run("uv", ["run", "--project", "backend", "python", "-c", `
import json,re,sys
from pathlib import Path
from fastapi import FastAPI
sys.path[:0]=['backend','backend/kernel']
from valuz_agent.api.routes.docs import router
app=FastAPI()
app.include_router(router)
spec=app.openapi()
paths={"/v1/docs/search":spec["paths"]["/v1/docs/search"]}
refs=set(re.findall(r'#/components/[^"\\\\ ]+',json.dumps(paths)))
parts={}
while refs:
    ref=min(refs)
    refs.remove(ref)
    _,_,kind,name=ref.split('/')
    group=parts.setdefault(kind,{})
    if name in group: continue
    value=spec['components'][kind][name]
    group[name]=value
    refs.update(re.findall(r'#/components/[^"\\\\ ]+',json.dumps(value)))
output={k:v for k,v in spec.items() if k in {'openapi','info'}}
output.update(paths=paths,components=parts)
Path(sys.argv[1]).write_text(json.dumps(output))
`, contract], repo);
  run("npm", ["exec", "--yes", "--package=openapi-typescript@7.12.0", "--",
    "openapi-typescript", contract, "--default-non-nullable", "false", "-o", "packages/core/src/api/generated/docs.ts"], frontend);
} finally {
  rmSync(temp, { recursive: true, force: true });
}

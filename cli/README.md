# Valuz Product CLI (Go)

The user-facing `valuz` command — runtime control plane for the backend,
WebUI, Desktop, and TUI hosts, plus the headless execution command
surface (run / session / task / project / activity / resource / auth /
env / model / agent). Language and ownership decisions live in
[`docs/STRUCTURE.md`](../docs/STRUCTURE.md) §"Language Decisions" and
§"Build Artifact Names".

## Lifecycle (local runtime)

- `valuz start [all|backend|frontend] [-f]` — spawns dev services directly
  via `os/exec` (no shell wrapper). Logs at `.ai/dev/{backend,frontend}.log`,
  PIDs at `.ai/dev/valuz.pid`. Flags: `--port`, `--host`, `--reload`,
  `--backend-arg` (repeatable), `--frontend-arg` (repeatable). Anything
  after `--` is appended verbatim to the backend command line.
- `valuz stop [--force]` — reads `.ai/dev/valuz.pid` and SIGTERMs each
  PID's process group; falls back to pattern matching for orphans.
- `valuz status` — HTTP probe of `:8000/v1/projects` + lsof PIDs.
- `valuz logs [backend|frontend|launch] [--follow] [-n]` — tail dev logs.
- `valuz doctor` — `uv` / `pnpm` / `node` / `go` + key paths +
  launchd plist + writer lock + backend probe.
- `valuz install-autostart [--port 8000]` — writes the macOS launchd plist
- `valuz uninstall-autostart`

## Headless execution (product command surface)

The connect-only headless surface talks to a running backend over HTTP:

- `valuz run` — one turn in a new session, blocking until terminal
  (`--project`/`--cwd`, `--agent`, `--mcp`, `--skill`, `--model`,
  `--provider`, `--runtime`, `--permission-mode`, `--timeout`,
  `--output human|json|jsonl`, `--trajectory`)
- `valuz session` — create / list / show / interrupt / events(--stream) /
  send / approve
- `valuz task` — kickoff / list / show / events(--stream) / wait /
  intervene / commit / abandon / inject / plan
- `valuz project` — list / show / create / members / deploy
- `valuz activity` — running/finished cross-project overview
- `valuz resource` — agents / agent <slug> / skills / connectors
- `valuz auth` — login (email+password or vzp_ api key) / status / logout
- `valuz env` — use local|cloud · list · show · set (custom backend URL)
- `valuz model` — list (runtime/provider filters) · use (pin default)
- `valuz agent` — list / show / use (pin default)

Exit codes follow the stable contract (0 completed / 1 usage / 2 timeout /
3 agent error / 4 backend unreachable / 5 internal / 6 auth / 7 action
required / 130 SIGINT / 143 SIGTERM). `run --output jsonl` emits the
`valuz.run-event/v1` event stream with an exactly-once `run.end` terminal
line; `--output json` emits the `valuz.run-result/v1` document.

Stubs (print "not implemented" and exit non-zero):

- `valuz web`, `valuz desktop`, `valuz tui` — frontend host launchers,
  pending Phase 3 (packaged-binary discovery).

## Build & run

```bash
cd cli
go build -o /tmp/valuz .         # build binary
/tmp/valuz --help

# Or run directly without producing a binary
go run . --help
go run . status
go run . start
```

## Module layout

```
cli/
├── go.mod
├── main.go
└── internal/
    ├── cmd/                  # cobra command definitions
    ├── proc/                 # managed-subprocess spawner (replaces scripts/dev.sh)
    ├── backend/              # HTTP clients (control + SSE stream) and DTOs
    ├── auth/                 # control-plane login state + token refresh
    ├── config/               # profile store + resolution precedence
    ├── errors/               # typed error boundary (kind → exit code, redaction)
    ├── output/               # RunResult / RunEvent contracts + sink
    ├── runner/               # connect-only run orchestration (turn machine)
    ├── turn/                 # message_id turn state machine
    ├── event/                # SSE frame → typed payload mapping
    ├── project/              # cwd → project resolution
    ├── runtime/              # path / discovery helpers
    └── version/              # version + client-identity headers
```
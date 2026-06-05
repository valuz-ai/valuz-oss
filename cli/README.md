# Valuz Product CLI (Go)

The user-facing `valuz` command — runtime control plane for the backend,
WebUI, Desktop, and TUI hosts. Language and ownership decisions live in
[`docs/STRUCTURE.md`](../docs/STRUCTURE.md) §"Language Decisions" and
§"Build Artifact Names".

## Working today

Lifecycle:

- `valuz start [all|backend|frontend] [-f]` — spawns dev services directly
  via `os/exec` (no shell wrapper). Logs at `.ai/dev/{backend,frontend}.log`,
  PIDs at `.ai/dev/valuz.pid`. Flags: `--port`, `--host`, `--reload`,
  `--backend-arg` (repeatable), `--frontend-arg` (repeatable). Anything
  after `--` is appended verbatim to the backend command line.
- `valuz stop [--force]` — reads `.ai/dev/valuz.pid` and SIGTERMs each
  PID's process group; falls back to pattern matching for orphans.
- `valuz status` — HTTP probe of `:8000/v1/workspaces` + lsof PIDs.
- `valuz logs [backend|frontend|launch] [--follow] [-n]` — tail dev logs.
- `valuz doctor` — `uv` / `pnpm` / `node` / `go` + key paths +
  launchd plist + writer lock + backend probe.

Power-user (talks to the running backend over HTTP):

- `valuz schedule {list,show,run,pause,resume,delete} [task-id]`
- `valuz install-autostart [--port 8000]` — writes the macOS launchd plist
- `valuz uninstall-autostart`

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
    ├── backend/              # HTTP client for the running valuz-server
    └── runtime/              # path / discovery helpers
```

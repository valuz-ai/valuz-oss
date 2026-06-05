# @valuz/tui

Terminal UI host. Placeholder for Phase 1.

The TUI is a frontend host (TypeScript), launched by the `valuz tui`
command from the Go product CLI. It talks to the backend through the same
product APIs as the WebUI and Desktop hosts.

## Status

Phase 1 — placeholder only. Just enough package metadata to be detected by
the pnpm workspace (`@valuz/tui`) and pass `pnpm typecheck` / `pnpm lint`.

No terminal rendering library is wired up. When the TUI is actually
implemented it will use Ink or OpenTUI (per `docs/STRUCTURE.md` §"TUI:
TypeScript").

## Run

```bash
pnpm --filter @valuz/tui start
# → "Valuz TUI placeholder — not implemented yet."
```

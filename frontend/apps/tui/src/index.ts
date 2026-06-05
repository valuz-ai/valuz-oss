// Valuz terminal UI host. Placeholder — see docs/STRUCTURE.md §"TUI".
//
// The TUI is a frontend host (TypeScript), not the product CLI. It is
// launched by `valuz tui` and talks to the backend through the same product
// APIs as the WebUI and Desktop hosts. Implementation will use Ink or
// OpenTUI; nothing is wired up yet.

const main = (): void => {
  process.stdout.write("Valuz TUI placeholder — not implemented yet.\n");
};

main();

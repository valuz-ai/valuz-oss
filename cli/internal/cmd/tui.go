package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
)

// newTUICmd registers `valuz tui`. The TUI host scaffold exists at
// frontend/apps/tui (TypeScript placeholder) per docs/STRUCTURE.md
// §"TUI", but the actual terminal-rendering implementation (Ink /
// OpenTUI) hasn't been written yet — this command stays a stub until
// then.
func newTUICmd() *cobra.Command {
	return &cobra.Command{
		Use:   "tui",
		Short: "Launch the TUI host",
		RunE: func(_ *cobra.Command, _ []string) error {
			return fmt.Errorf(
				"`valuz tui` is not implemented yet — " +
					"frontend/apps/tui is a placeholder; no terminal UI to launch",
			)
		},
	}
}

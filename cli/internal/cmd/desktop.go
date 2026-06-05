package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
)

func newDesktopCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "desktop",
		Short: "Launch the Desktop host",
		RunE: func(_ *cobra.Command, _ []string) error {
			return fmt.Errorf(
				"`valuz desktop` is not implemented yet.\n" +
					"  In a packaged install: open /Applications/Valuz.app\n" +
					"  In dev: ./scripts/dev.sh frontend (or pnpm --filter @valuz/desktop dev)",
			)
		},
	}
}

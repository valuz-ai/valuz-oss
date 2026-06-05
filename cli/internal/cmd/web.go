package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
)

func newWebCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "web",
		Short: "Launch the WebUI host",
		RunE: func(_ *cobra.Command, _ []string) error {
			return fmt.Errorf(
				"`valuz web` is not implemented yet.\n" +
					"  In dev: pnpm --filter @valuz/webui dev",
			)
		},
	}
}

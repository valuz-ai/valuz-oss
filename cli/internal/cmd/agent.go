package cmd

import (
	"github.com/spf13/cobra"
)

// newAgentCmd builds the top-level `valuz agent ...` command — agent
// selection for the run path. Browsing lives in `valuz resource`
// (agents / agent <slug>), keeping one ownership per command surface.
func newAgentCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "agent",
		Short: "Select the default agent for run (browse: valuz resource)",
	}
	cmd.AddCommand(
		newAgentUseCmd(),
	)
	return cmd
}

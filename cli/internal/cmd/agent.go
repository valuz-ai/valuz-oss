package cmd

import (
	"github.com/spf13/cobra"
)

// newAgentCmd builds the top-level `valuz agent ...` command — agent
// discovery and selection. Browse lives here (list/show) as well as the
// run-path default pinning (use); the resource library keeps its own
// grouped views.
func newAgentCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "agent",
		Short: "Manage agents (execution identities)",
	}
	cmd.AddCommand(
		newResourceAgentsCmd(),
		newResourceAgentShowCmd(),
		newAgentUseCmd(),
	)
	return cmd
}

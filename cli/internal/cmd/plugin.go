package cmd

import "github.com/spf13/cobra"

// newPluginCmd is the single public entry point for all plugin families.
// Manifest formats and HTTP endpoints belong to each family; they are not
// interchangeable even though lifecycle verbs share the same names.
func newPluginCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "plugin",
		Short: "Manage agent, app, built-in and DSH plugins",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return cmd.Help()
		},
		Long: "Choose the plugin family:\n\n" +
			"  agent    skills and MCP connectors (Agent Plugins)\n" +
			"  app      application pages, panels and automations (valuz-plugin.json)\n" +
			"  builtin  built-in backend plugins (changes may need a restart)\n" +
			"  dsh      native plugins of the managed DSH profile",
	}
	cmd.AddCommand(newAgentPluginCmd(), newAppPluginCmd(), newBuiltinPluginCmd(), newDshPluginCmd())
	return cmd
}

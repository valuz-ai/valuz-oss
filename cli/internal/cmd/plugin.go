package cmd

import (
	"fmt"
	"github.com/spf13/cobra"
)

// newPluginCmd is the single public entry point for all plugin families.
// Manifest formats and HTTP endpoints belong to each family; they are not
// interchangeable even though lifecycle verbs share the same names.
func newPluginCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "plugin",
		Short: "Manage agent, app, built-in and DSH plugins",
		Long: "Choose the plugin family:\n\n" +
			"  agent    skills and MCP connectors (Agent Plugins)\n" +
			"  app      application pages, panels and automations (valuz-plugin.json)\n" +
			"  builtin  built-in backend plugins (changes may need a restart)\n" +
			"  dsh      native plugins of the managed DSH profile",
	}
	cmd.AddCommand(newAgentPluginCmd(), newAppPluginCmd(), newBuiltinPluginCmd(), newDshPluginCmd())
	// The unqualified verbs previously managed app plugins. Preserve scripts
	// while keeping the help/completion surface unambiguous.
	for _, legacy := range newAppPluginCommands() {
		legacy.Hidden = true
		warnLegacyPluginCommand(legacy, "valuz plugin app "+legacy.Name())
		cmd.AddCommand(legacy)
	}
	return cmd
}

// warnLegacyPluginCommand writes compatibility notices to stderr. Cobra's
// Deprecated field writes to stdout, which would break `-o json` consumers.
func warnLegacyPluginCommand(cmd *cobra.Command, replacement string) {
	if run := cmd.RunE; run != nil {
		cmd.RunE = func(cmd *cobra.Command, args []string) error {
			fmt.Fprintf(cmd.ErrOrStderr(), "Deprecated: use `%s` instead of `%s`.\n", replacement, cmd.CommandPath())
			return run(cmd, args)
		}
	}
}

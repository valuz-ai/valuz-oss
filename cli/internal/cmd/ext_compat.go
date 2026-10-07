package cmd

import "github.com/spf13/cobra"

// newExtCmd retains the old `valuz ext ...` spelling as a hidden compatibility
// entry. Canonical commands are `valuz plugin builtin ...` and `plugin dsh ...`.
// (plugin-architecture design §8). Two halves, both through the backend so
// the CLI never needs to know the data directory or the dsh launcher:
//
//   - Valuz's built-in backend plugins: GET/POST /v1/builtin-plugins…
//     Toggles are recorded and applied at the next start.
//   - dsh plugins in the managed dsh profile: /v1/dsh/plugins/remote/<m>,
//     which forwards to dsh's own PluginManager — so install / enable /
//     remove behave exactly as `dsh plugin` does. Result shapes are dsh's
//     and kept as generic JSON here, so an upstream field never breaks the CLI.
func newExtCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:    "ext",
		Short:  "Compatibility entry for plugin builtin and plugin dsh",
		Long:   "Use `valuz plugin builtin` for built-in backend plugins and `valuz plugin dsh` for native DSH plugins.",
		Hidden: true,
	}
	for _, child := range newBuiltinPluginCommands() {
		warnLegacyPluginCommand(child, "valuz plugin builtin "+child.Name())
		cmd.AddCommand(child)
	}
	dsh := newDshPluginCmd()
	for _, child := range dsh.Commands() {
		warnLegacyPluginCommand(child, "valuz plugin dsh "+child.Name())
	}
	cmd.AddCommand(dsh)
	return cmd
}

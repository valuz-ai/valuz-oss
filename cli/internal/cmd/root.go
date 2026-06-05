// Package cmd holds the cobra command tree for the valuz CLI.
package cmd

import "github.com/spf13/cobra"

// Root returns the configured root command. Tests and main() both call this
// rather than relying on a package-level singleton.
func Root() *cobra.Command {
	root := &cobra.Command{
		Use:           "valuz",
		Short:         "Valuz product CLI",
		Long:          "Valuz product CLI — start, stop, and inspect local runtime services.",
		SilenceUsage:  true,
		SilenceErrors: true,
	}
	root.AddCommand(
		newStartCmd(),
		newStopCmd(),
		newRestartCmd(),
		newStatusCmd(),
		newLogsCmd(),
		newDoctorCmd(),
		newInstallAutostartCmd(),
		newUninstallAutostartCmd(),
		newWebCmd(),
		newDesktopCmd(),
		newTUICmd(),
	)
	return root
}

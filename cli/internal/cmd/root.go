package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
)

var cliVersion = "dev"

// SetVersion is called by main() to inject the build-time version (via ldflags).
func SetVersion(v string) { cliVersion = v }

// Root returns the configured root command. Tests and main() both call this
// rather than relying on a package-level singleton.
func Root() *cobra.Command {
	root := &cobra.Command{
		Use:           "valuz",
		Short:         "Valuz product CLI",
		Long:          "Valuz product CLI — start, stop, and inspect local runtime services.",
		Version:       cliVersion,
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
		newVersionCmd(),
	)
	return root
}

func newVersionCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "version",
		Short: "Print the CLI version",
		Run: func(_ *cobra.Command, _ []string) {
			fmt.Println(cliVersion)
		},
	}
}

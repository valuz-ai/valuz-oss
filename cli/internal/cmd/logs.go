package cmd

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/runtime"
)

var logTargets = map[string]string{
	"backend":  "backend.log",
	"frontend": "frontend.log",
	"launch":   "launch.log",
}

func newLogsCmd() *cobra.Command {
	var follow bool
	var lines int
	c := &cobra.Command{
		Use:       "logs [backend|frontend|launch]",
		Short:     "Tail runtime service logs",
		ValidArgs: []string{"backend", "frontend", "launch"},
		Args:      cobra.MatchAll(cobra.MaximumNArgs(1), cobra.OnlyValidArgs),
		RunE: func(_ *cobra.Command, args []string) error {
			target := "backend"
			if len(args) == 1 {
				target = args[0]
			}
			paths, err := runtime.Discover()
			if err != nil {
				return err
			}
			file := filepath.Join(paths.LogDir, logTargets[target])
			if _, err := os.Stat(file); err != nil {
				return fmt.Errorf("log not found: %s", file)
			}
			tailArgs := []string{"-n" + strconv.Itoa(lines)}
			if follow {
				tailArgs = append(tailArgs, "-f")
			}
			tailArgs = append(tailArgs, file)
			cmd := exec.Command("tail", tailArgs...)
			cmd.Stdout = os.Stdout
			cmd.Stderr = os.Stderr
			return cmd.Run()
		},
	}
	c.Flags().BoolVarP(&follow, "follow", "f", true, "Follow output (set --follow=false to disable)")
	c.Flags().IntVarP(&lines, "lines", "n", 200, "Initial lines to print")
	return c
}

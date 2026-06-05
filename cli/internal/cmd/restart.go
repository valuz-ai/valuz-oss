package cmd

import (
	"fmt"
	"os"
	"strconv"
	"time"

	"github.com/spf13/cobra"
)

// newRestartCmd implements “valuz restart“: a stop+start convenience
// that accepts every flag “valuz start“ accepts plus “--force“ to
// pass through to the stop phase.
func newRestartCmd() *cobra.Command {
	var (
		foreground   bool
		force        bool
		host         string
		port         int
		reload       bool
		backendArgs  []string
		frontendArgs []string
	)
	c := &cobra.Command{
		Use:   "restart [all|backend|frontend] [-- extra args]",
		Short: "Stop locally-running runtime services, then start them again",
		Long: `Stop + start in one shot.

Useful after editing backend code without --reload, or after switching
ports. Equivalent to running 'valuz stop' then 'valuz start' — but
the in-between sleep is shortened and any unrecoverable error from the
stop phase is surfaced rather than swallowed.`,
		Args: cobra.ArbitraryArgs,
		RunE: func(cobraCmd *cobra.Command, args []string) error {
			target, extras, err := splitTargetAndPassthrough(args, cobraCmd.ArgsLenAtDash())
			if err != nil {
				return err
			}
			backendArgs = append(backendArgs, extras...)

			if !cobraCmd.Flags().Changed("port") {
				if v := os.Getenv("VALUZ_BACKEND_PORT"); v != "" {
					if n, perr := strconv.Atoi(v); perr == nil {
						port = n
					}
				}
			}
			if !cobraCmd.Flags().Changed("reload") && os.Getenv("VALUZ_RELOAD") == "1" {
				reload = true
			}

			fmt.Println("[valuz] restart: stopping current services …")
			if err := RunStop(force); err != nil {
				return fmt.Errorf("stop phase: %w", err)
			}
			// Brief settle window so kernel TIME_WAIT clears and the
			// listening socket is free for the new backend.
			time.Sleep(500 * time.Millisecond)
			fmt.Println("[valuz] restart: starting services …")
			return RunStart(StartOptions{
				Target:       target,
				Host:         host,
				Port:         port,
				Reload:       reload,
				BackendArgs:  backendArgs,
				FrontendArgs: frontendArgs,
				Foreground:   foreground,
			})
		},
	}
	c.Flags().BoolVarP(&foreground, "foreground", "f", false, "Run in foreground (Ctrl+C stops every child)")
	c.Flags().BoolVar(&force, "force", false, "Force the stop phase to SIGKILL immediately")
	c.Flags().StringVar(&host, "host", "127.0.0.1", "Backend bind host")
	c.Flags().IntVarP(&port, "port", "p", 8000, "Backend bind port (env: VALUZ_BACKEND_PORT)")
	c.Flags().BoolVar(&reload, "reload", false, "Backend auto-reload (env: VALUZ_RELOAD=1)")
	c.Flags().StringArrayVar(&backendArgs, "backend-arg", nil, "Extra backend arg (repeatable)")
	c.Flags().StringArrayVar(&frontendArgs, "frontend-arg", nil, "Extra pnpm arg (repeatable)")
	return c
}

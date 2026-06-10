package cmd

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/proc"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/runtime"
)

// StartOptions captures everything needed to (re)start runtime services.
// Exposed so `valuz restart` can reuse the same launch path.
type StartOptions struct {
	Target       string // "all" | "backend" | "frontend"
	Host         string
	Port         int
	Reload       bool
	BackendArgs  []string
	FrontendArgs []string
	Foreground   bool
}

// RunStart is the package-level entrypoint shared by `valuz start` and
// `valuz restart`. Returns non-nil when either the spawn fails or a
// readiness probe times out (children are left running on probe
// failure — call RunStop to clean them up).
func RunStart(opts StartOptions) error {
	if opts.Target == "" {
		opts.Target = "all"
	}
	if opts.Target != "all" && opts.Target != "backend" && opts.Target != "frontend" {
		return fmt.Errorf("unknown target %q (expected: all | backend | frontend)", opts.Target)
	}

	paths, err := runtime.Discover()
	if err != nil {
		return err
	}
	// ``valuz start`` is a dev-mode command — it spawns ``uv run python``
	// and ``pnpm`` which only exist in a checkout. In a packaged
	// Valuz.app the launcher is Electron itself (via sidecar.ts), and
	// for headless backend startup the right tool is install-autostart.
	if paths.Mode != runtime.ModeDev {
		return fmt.Errorf(
			"`valuz start` is for development only — "+
				"detected %s install at %s.\n"+
				"To launch the backend headless: `valuz install-autostart`\n"+
				"To use the full GUI: open /Applications/Valuz.app",
			paths.Mode, paths.LibexecDir,
		)
	}

	specs := buildSpecs(paths, opts.Target, opts.Host, opts.Port, opts.Reload, opts.BackendArgs, opts.FrontendArgs)
	specNames := make([]string, 0, len(specs))
	for _, s := range specs {
		specNames = append(specNames, s.Name)
	}

	conflicts, err := proc.PrecheckRunning(paths.LogDir, specNames)
	if err != nil {
		return fmt.Errorf("read PID file: %w", err)
	}
	if len(conflicts) > 0 {
		return fmt.Errorf(
			"already running: %s — run `valuz stop` or `valuz restart` first",
			strings.Join(conflicts, ", "),
		)
	}

	fmt.Printf("[valuz] starting %d service(s) (foreground=%v, log-dir=%s)\n",
		len(specs), opts.Foreground, paths.LogDir)

	running, spawnErr := proc.Spawn(specs, paths.LogDir, opts.Foreground)
	if !opts.Foreground {
		for _, r := range running {
			fmt.Printf("[valuz] %-8s pid=%d log=%s\n", r.Spec.Name, r.Cmd.Process.Pid, r.LogFile)
		}
		if len(running) > 0 {
			fmt.Printf("[valuz] %d service(s) running in background; pid file: %s\n",
				len(running), paths.LogDir+"/valuz.pid")
		}
	}
	return spawnErr
}

func newStartCmd() *cobra.Command {
	var (
		foreground   bool
		host         string
		port         int
		reload       bool
		backendArgs  []string
		frontendArgs []string
	)
	c := &cobra.Command{
		Use:   "start [all|backend|frontend] [-- extra args]",
		Short: "Start runtime services (backend + frontend dev shell)",
		Long: `Start runtime services in dev mode.

Spawns the backend (uv run python -m valuz_agent) and/or the desktop
frontend (pnpm --filter @valuz/desktop dev) directly — no shell wrapper.
Logs land under .ai/dev/{backend,frontend}.log and PIDs are recorded at
.ai/dev/valuz.pid so 'valuz stop' can shut them down cleanly.

Arguments after a literal '--' are appended verbatim to the BACKEND
command line, mirroring the bash convention:

    valuz start backend -- --workers 2 --no-access-log

VALUZ_BACKEND_PORT env still works but the explicit --port flag wins
when both are set.`,
		Args: cobra.ArbitraryArgs,
		RunE: func(cobraCmd *cobra.Command, args []string) error {
			target, extras, err := splitTargetAndPassthrough(args, cobraCmd.ArgsLenAtDash())
			if err != nil {
				return err
			}
			backendArgs = append(backendArgs, extras...)

			// VALUZ_BACKEND_PORT compat: only when --port wasn't passed.
			if !cobraCmd.Flags().Changed("port") {
				if v := os.Getenv("VALUZ_BACKEND_PORT"); v != "" {
					if n, perr := strconv.Atoi(v); perr == nil {
						port = n
					}
				}
			}
			// VALUZ_RELOAD compat.
			if !cobraCmd.Flags().Changed("reload") && os.Getenv("VALUZ_RELOAD") == "1" {
				reload = true
			}

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
	c.Flags().StringVar(&host, "host", "127.0.0.1", "Backend bind host")
	c.Flags().IntVarP(&port, "port", "p", 8000, "Backend bind port (env: VALUZ_BACKEND_PORT)")
	c.Flags().BoolVar(&reload, "reload", false, "Backend auto-reload via uvicorn (env: VALUZ_RELOAD=1)")
	c.Flags().StringArrayVar(&backendArgs, "backend-arg", nil,
		"Extra backend arg (repeatable). Same effect as `-- <arg>`. e.g. --backend-arg=--workers --backend-arg=2")
	c.Flags().StringArrayVar(&frontendArgs, "frontend-arg", nil,
		"Extra pnpm arg appended to the frontend filter (repeatable).")
	return c
}

// splitTargetAndPassthrough teases apart cobra's positional args into
// the optional target name and the bash-style `--` pass-through tail.
func splitTargetAndPassthrough(args []string, dash int) (target string, extras []string, err error) {
	var head []string
	if dash >= 0 {
		head = args[:dash]
		extras = args[dash:]
	} else {
		head = args
	}
	if len(head) > 1 {
		return "", nil, fmt.Errorf("at most one target accepted (got %v); use `--` to separate pass-through args", head)
	}
	target = "all"
	if len(head) == 1 {
		target = head[0]
	}
	return target, extras, nil
}

func buildSpecs(p *runtime.Paths, target, host string, port int, reload bool, backendArgs, frontendArgs []string) []proc.Spec {
	var specs []proc.Spec
	if target == "all" || target == "backend" {
		args := []string{"run", "python", "-m", "valuz_agent",
			"--host", host, "--port", strconv.Itoa(port)}
		if reload {
			args = append(args, "--reload")
		}
		args = append(args, backendArgs...)
		specs = append(specs, proc.Spec{
			Name:         "backend",
			Bin:          "uv",
			Args:         args,
			Cwd:          p.BackendDir,
			ReadyURL:     fmt.Sprintf("http://%s:%d/v1/projects", host, port),
			ReadyTimeout: 30 * time.Second,
		})
	}
	if target == "all" || target == "frontend" {
		args := []string{"--filter", "@valuz/desktop", "dev"}
		args = append(args, frontendArgs...)
		specs = append(specs, proc.Spec{
			Name: "frontend",
			Bin:  "pnpm",
			Args: args,
			Cwd:  p.FrontendDir,
		})
	}
	return specs
}

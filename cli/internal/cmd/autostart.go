package cmd

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/spf13/cobra"

	cliruntime "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/runtime"
)

func newInstallAutostartCmd() *cobra.Command {
	var port int
	var executable string
	c := &cobra.Command{
		Use:   "install-autostart",
		Short: "Install auto-start entry so the backend starts on login",
		RunE: func(cobraCmd *cobra.Command, _ []string) error {
			// If --port wasn't passed, pick the mode-appropriate default
			// (bundle = 19100 matches Electron sidecar; dev = 8000).
			if !cobraCmd.Flags().Changed("port") {
				if paths, perr := cliruntime.Discover(); perr == nil && paths.BackendPort > 0 {
					port = paths.BackendPort
				}
			}
			exe, err := resolveServerExe(executable)
			if err != nil {
				return err
			}
			logDir, err := defaultLogDir()
			if err != nil {
				return err
			}
			return installAutostartPlatform(exe, port, logDir)
		},
	}
	c.Flags().IntVar(&port, "port", 0, "Backend port to bind (default: 19100 in bundle mode, 8000 in dev)")
	c.Flags().StringVar(&executable, "executable", "",
		"Absolute path to the valuz-server binary. Auto-detected from the install layout when omitted.")
	return c
}

func newUninstallAutostartCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "uninstall-autostart",
		Short: "Remove the auto-start entry",
		RunE: func(_ *cobra.Command, _ []string) error {
			return uninstallAutostartPlatform()
		},
	}
}

func defaultLogDir() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	// Matches valuz_agent.infra.config.settings.log_dir default.
	return filepath.Join(home, ".valuz", "logs"), nil
}

// resolveServerExe locates the absolute path of the valuz-server binary.
//
// Order:
//  1. Explicit --executable
//  2. Bundle mode: Paths.ServerExe (resolved by runtime.Discover)
//  3. Dev mode: <repo>/backend/dist/valuz-server/valuz-server
func resolveServerExe(override string) (string, error) {
	if override != "" {
		abs, err := filepath.Abs(override)
		if err != nil {
			return "", err
		}
		if _, err := os.Stat(abs); err != nil {
			return "", fmt.Errorf("executable not found: %s", abs)
		}
		return abs, nil
	}

	paths, err := cliruntime.Discover()
	if err == nil {
		if paths.ServerExe != "" {
			return paths.ServerExe, nil
		}
		if paths.BackendDir != "" {
			candidate := filepath.Join(paths.BackendDir, "dist", "valuz-server", "valuz-server")
			if _, err := os.Stat(candidate); err == nil {
				return candidate, nil
			}
		}
	}

	return "", fmt.Errorf(
		"could not locate valuz-server binary. " +
			"Build it first (scripts/build-desktop.sh --skip-frontend) or pass --executable",
	)
}

func trim(b []byte) string {
	s := string(b)
	for len(s) > 0 && (s[len(s)-1] == '\n' || s[len(s)-1] == ' ') {
		s = s[:len(s)-1]
	}
	return s
}

// Shared helper: shell out and return trimmed output + error.
func cmdOutput(name string, args ...string) (string, error) {
	out, err := exec.Command(name, args...).CombinedOutput()
	return trim(out), err
}

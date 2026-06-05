package cmd

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"

	"github.com/spf13/cobra"

	cliruntime "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/runtime"
)

// macOS launchd label.
const launchdLabel = "io.valuz.oss"

func newInstallAutostartCmd() *cobra.Command {
	var port int
	var executable string
	c := &cobra.Command{
		Use:   "install-autostart",
		Short: "Install macOS launchd plist so the backend starts on login",
		RunE: func(cobraCmd *cobra.Command, _ []string) error {
			if runtime.GOOS != "darwin" {
				return errors.New(
					"install-autostart only supports macOS today. " +
						"Linux systemd / Windows Task Scheduler templates are tracked in ADR-011",
				)
			}
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
			if err := os.MkdirAll(logDir, 0o755); err != nil {
				return err
			}
			plistPath, err := launchdPlistPath()
			if err != nil {
				return err
			}
			if err := os.MkdirAll(filepath.Dir(plistPath), 0o755); err != nil {
				return err
			}
			plist := renderLaunchdPlist(exe, port, logDir)
			if err := os.WriteFile(plistPath, []byte(plist), 0o644); err != nil {
				return err
			}
			// ``unload`` first to keep the operation idempotent if a prior
			// install with the same label is already loaded.
			_ = exec.Command("launchctl", "unload", plistPath).Run()
			loadOut, err := exec.Command("launchctl", "load", plistPath).CombinedOutput()
			if err != nil {
				return fmt.Errorf("launchctl load failed: %s", trim(loadOut))
			}
			fmt.Printf("installed %s (port=%d, exe=%s)\n", plistPath, port, exe)
			fmt.Println("backend will start automatically at next login (and now).")
			return nil
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
		Short: "Remove the macOS launchd plist",
		RunE: func(_ *cobra.Command, _ []string) error {
			if runtime.GOOS != "darwin" {
				return errors.New("uninstall-autostart only supports macOS today")
			}
			plistPath, err := launchdPlistPath()
			if err != nil {
				return err
			}
			if _, err := os.Stat(plistPath); errors.Is(err, os.ErrNotExist) {
				fmt.Println("nothing to uninstall (plist not found).")
				return nil
			}
			_ = exec.Command("launchctl", "unload", plistPath).Run()
			if err := os.Remove(plistPath); err != nil && !errors.Is(err, os.ErrNotExist) {
				return err
			}
			fmt.Printf("removed %s\n", plistPath)
			return nil
		},
	}
}

func launchdPlistPath() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, "Library", "LaunchAgents", launchdLabel+".plist"), nil
}

func defaultLogDir() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	// Matches valuz_agent.infra.config.settings.log_dir default.
	return filepath.Join(home, ".valuz", "logs"), nil
}

// resolveServerExe locates the absolute path of the valuz-server binary the
// plist should launch.
//
// Order:
//  1. Explicit --executable
//  2. Bundle mode: “Paths.ServerExe“ (resolved by runtime.Discover)
//  3. Dev mode: “<repo>/backend/dist/valuz-server/valuz-server“
//     (PyInstaller's --distpath keeps the wrapper dir even though the
//     Desktop staging flattens it)
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

	return "", errors.New(
		"could not locate valuz-server binary. " +
			"Build it first (scripts/build-desktop.sh --skip-frontend) or pass --executable",
	)
}

func renderLaunchdPlist(exe string, port int, logDir string) string {
	home, _ := os.UserHomeDir()
	return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>` + launchdLabel + `</string>
  <key>ProgramArguments</key>
  <array>
    <string>` + exe + `</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>` + strconv.Itoa(port) + `</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>` + home + `</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>
  <dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardOutPath</key><string>` + logDir + `/launchd.out.log</string>
  <key>StandardErrorPath</key><string>` + logDir + `/launchd.err.log</string>
  <key>ThrottleInterval</key><integer>5</integer>
</dict>
</plist>
`
}

func trim(b []byte) string {
	s := string(b)
	for len(s) > 0 && (s[len(s)-1] == '\n' || s[len(s)-1] == ' ') {
		s = s[:len(s)-1]
	}
	return s
}

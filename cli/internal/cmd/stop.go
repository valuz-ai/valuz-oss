package cmd

import (
	"context"
	"fmt"
	"os/exec"
	goruntime "runtime"
	"syscall"
	"time"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/proc"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/runtime"
)

// Fallback name patterns for when the PID file is missing (e.g. when
// the user previously launched things outside of `valuz start`, or is
// running against a packaged install). Kept in sync with what start.go
// and sidecar.ts spawn.
var stopFallbackPatterns = []string{
	"valuz_agent --host",            // dev: `uv run python -m valuz_agent --host …`
	"valuz-server --host",           // production: PyInstaller bundle started by Electron / launchd
	"pnpm.*--filter @valuz/desktop", // dev frontend
	"concurrently.*vite",            // dev frontend grand-child
}

// RunStop is the package-level entrypoint for “valuz stop“ /
// “valuz restart“. force=true skips the SIGTERM grace period and
// goes straight to SIGKILL.
func RunStop(force bool) error {
	paths, err := runtime.Discover()
	if err != nil {
		// Tolerate unknown layout — still try the fallback sweep below.
		paths = &runtime.Paths{}
	}

	// Bundle mode → prefer asking Valuz.app to quit cleanly via
	// AppleScript. Electron's sidecar then runs its own teardown and
	// the backend exits gracefully, releasing the writer lock. The
	// subsequent pkill sweep still runs to catch any standalone
	// backend (e.g. launchd autostart) that's not owned by Valuz.app.
	quitGUI := false
	if paths.Mode == runtime.ModeBundle {
		if quitValuzApp() {
			fmt.Println("[valuz] sent quit to Valuz.app (Electron will tear down its sidecar)")
			quitGUI = true
		}
	}

	rec, err := proc.ReadPidFile(paths.LogDir)
	if err != nil {
		fmt.Printf("[valuz] warning: could not read PID file: %v\n", err)
	}

	sig := syscall.SIGTERM
	if force {
		sig = syscall.SIGKILL
	}

	signalled := 0
	for label, pid := range map[string]int{"backend": rec.Backend, "frontend": rec.Frontend} {
		if pid <= 0 {
			continue
		}
		if proc.StopByPid(pid, sig) {
			fmt.Printf("[valuz] sent %s to %s (pid=%d)\n", sig, label, pid)
			signalled++
		} else {
			fmt.Printf("[valuz] %s pid=%d not running\n", label, pid)
		}
	}

	// Wait briefly for the recorded PIDs to exit. After grace, escalate
	// to SIGKILL unless we were already KILL'ing.
	if signalled > 0 && !force {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		if rec.Backend > 0 {
			proc.WaitFor(ctx, rec.Backend)
		}
		if rec.Frontend > 0 {
			proc.WaitFor(ctx, rec.Frontend)
		}
		cancel()
		for _, pid := range []int{rec.Backend, rec.Frontend} {
			if pid > 0 && syscall.Kill(pid, 0) == nil {
				_ = syscall.Kill(-pid, syscall.SIGKILL)
				_ = syscall.Kill(pid, syscall.SIGKILL)
				fmt.Printf("[valuz] escalated to SIGKILL pid=%d\n", pid)
			}
		}
	}

	// Fallback sweep: anything matching the spawn fingerprints (covers
	// the case where the user launched services before the PID file
	// existed).
	fallbackKilled := false
	for _, pat := range stopFallbackPatterns {
		if pkillPattern(pat, sig) {
			fmt.Printf("[valuz] swept %q\n", pat)
			fallbackKilled = true
		}
	}

	if signalled == 0 && !fallbackKilled && !quitGUI {
		fmt.Println("[valuz] no running services found")
	}

	if rerr := proc.RemovePidFile(paths.LogDir); rerr != nil {
		fmt.Printf("[valuz] warning: could not remove PID file: %v\n", rerr)
	}
	return nil
}

func newStopCmd() *cobra.Command {
	var force bool
	c := &cobra.Command{
		Use:   "stop",
		Short: "Stop locally-running runtime services",
		Long: `Stop locally-running runtime services.

Looks up .ai/dev/valuz.pid first (recorded by 'valuz start') and
signals each PID's process group with SIGTERM; falls through to
process-name matching for any leftovers that weren't recorded.`,
		RunE: func(_ *cobra.Command, _ []string) error {
			return RunStop(force)
		},
	}
	c.Flags().BoolVar(&force, "force", false, "Skip the grace period and SIGKILL straight away")
	return c
}

// quitValuzApp asks Valuz.app to quit via AppleScript. Returns true when
// the app is detected and the quit message dispatched (whether or not
// the GUI immediately accepts the quit). On non-darwin or when the
// app isn't running, returns false. macOS only.
func quitValuzApp() bool {
	if goruntime.GOOS != "darwin" {
		return false
	}
	// pgrep returns exit 0 on match, 1 on no match. The Electron main
	// process command line contains "Valuz.app/Contents/MacOS/Valuz".
	if err := exec.Command("pgrep", "-f", "Valuz.app/Contents/MacOS").Run(); err != nil {
		return false
	}
	if err := exec.Command("osascript", "-e", `tell application "Valuz" to quit`).Run(); err != nil {
		fmt.Printf("[valuz] warning: osascript quit failed: %v\n", err)
		return false
	}
	return true
}

// pkillPattern returns true when pkill matched at least one process.
func pkillPattern(pattern string, sig syscall.Signal) bool {
	sigArg := "-TERM"
	if sig == syscall.SIGKILL {
		sigArg = "-KILL"
	}
	err := exec.Command("pkill", sigArg, "-f", pattern).Run()
	if exitErr, ok := err.(*exec.ExitError); ok {
		// pkill exits 1 when no match, 0 when match — both fine, only
		// 2+ is an actual failure we'd care about.
		return exitErr.ExitCode() == 0
	}
	return err == nil
}

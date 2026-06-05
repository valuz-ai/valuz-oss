package cmd

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	goruntime "runtime"
	"strings"
	"time"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/runtime"
)

func newDoctorCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "doctor",
		Short: "Diagnose the local environment",
		RunE: func(_ *cobra.Command, _ []string) error {
			paths, err := runtime.Discover()
			if err != nil {
				return err
			}
			fmt.Printf("repo root : %s\n", paths.RepoRoot)
			fmt.Printf("mode      : %s\n\n", paths.Mode)

			fmt.Println("required tools:")
			for _, tool := range []string{"uv", "pnpm", "node", "go"} {
				ok, info := probe(tool)
				printCheck(ok, fmt.Sprintf("%-6s %s", tool, info))
			}

			fmt.Println("\nkey paths:")
			for _, p := range []struct {
				label string
				path  string
			}{
				{"log dir   ", paths.LogDir},
				{"cli       ", paths.CliDir},
				{"backend   ", paths.BackendDir},
				{"frontend  ", paths.FrontendDir},
			} {
				_, err := os.Stat(p.path)
				printCheck(err == nil, fmt.Sprintf("%s %s", p.label, p.path))
			}

			fmt.Println()
			printAutostartStatus()

			fmt.Println()
			printSchedulerLockStatus()

			fmt.Println()
			printBackendProbe()

			return nil
		},
	}
}

func printAutostartStatus() {
	if goruntime.GOOS != "darwin" {
		fmt.Println("launchd plist: skipped (non-macOS)")
		return
	}
	plist, err := launchdPlistPath()
	if err != nil {
		fmt.Printf("launchd plist: error: %v\n", err)
		return
	}
	if _, err := os.Stat(plist); err == nil {
		fmt.Printf("launchd plist: installed at %s\n", plist)
	} else {
		fmt.Println("launchd plist: NOT installed")
	}
}

// printSchedulerLockStatus surfaces ~/.valuz/app/.scheduler.lock so the
// user can see which backend process last claimed the in-process scheduler.
func printSchedulerLockStatus() {
	home, err := os.UserHomeDir()
	if err != nil {
		fmt.Printf("writer lock file: error: %v\n", err)
		return
	}
	lockPath := filepath.Join(home, ".valuz", "app", ".scheduler.lock")
	info, err := os.Stat(lockPath)
	if err != nil {
		fmt.Println("writer lock file: not present (no recent backend)")
		return
	}
	pid := "?"
	if data, err := os.ReadFile(lockPath); err == nil {
		pid = strings.TrimSpace(string(data))
		if pid == "" {
			pid = "?"
		}
	}
	fmt.Printf("writer lock file: %s (size=%d, last PID = %s)\n", lockPath, info.Size(), pid)
}

func printBackendProbe() {
	base := backend.BaseURL()
	url := base + "/v1/system/status"
	fmt.Printf("backend probe: %s …\n", url)
	client := &http.Client{Timeout: 2 * time.Second}
	resp, err := client.Get(url)
	if err != nil {
		fmt.Printf("  → unreachable (%s)\n", classifyHTTPErr(err))
		return
	}
	defer resp.Body.Close()
	tag := "OK"
	if resp.StatusCode >= 400 {
		tag = "FAIL"
	}
	fmt.Printf("  → HTTP %d (%s)\n", resp.StatusCode, tag)
}

func classifyHTTPErr(err error) string {
	s := err.Error()
	switch {
	case strings.Contains(s, "connection refused"):
		return "refused"
	case strings.Contains(s, "timeout"), strings.Contains(s, "deadline"):
		return "timeout"
	default:
		return "error"
	}
}

func probe(tool string) (bool, string) {
	path, err := exec.LookPath(tool)
	if err != nil {
		return false, "not found on PATH"
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, tool, "--version").CombinedOutput()
	if err != nil {
		return true, fmt.Sprintf("present at %s (version probe failed)", path)
	}
	first := strings.SplitN(strings.TrimSpace(string(out)), "\n", 2)[0]
	return true, first
}

func printCheck(ok bool, body string) {
	tag := "[ok ]"
	if !ok {
		tag = "[MISS]"
	}
	fmt.Printf("  %s %s\n", tag, body)
}

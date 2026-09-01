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

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/runtime"
)

func newDoctorCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "doctor",
		Short: "Diagnose the local environment",
		RunE: func(cmd *cobra.Command, _ []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			paths, err := runtime.Discover()
			if err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "repo root : %s\n", paths.RepoRoot)
			fmt.Fprintf(cmd.OutOrStdout(), "mode      : %s\n\n", paths.Mode)

			fmt.Fprintln(cmd.OutOrStdout(), "required tools:")
			for _, tool := range []string{"uv", "pnpm", "node", "go"} {
				ok, info := probe(tool)
				printCheck(cmd, ok, fmt.Sprintf("%-6s %s", tool, info))
			}

			fmt.Fprintln(cmd.OutOrStdout(), "\nkey paths:")
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
				printCheck(cmd, err == nil, fmt.Sprintf("%s %s", p.label, p.path))
			}

			fmt.Fprintln(cmd.OutOrStdout())
			printAutostartStatus(cmd)

			fmt.Fprintln(cmd.OutOrStdout())
			printWriterLockStatus(cmd)

			fmt.Fprintln(cmd.OutOrStdout())
			printBackendProbe(cmd, opts)

			return nil
		},
	}
}

func printAutostartStatus(cmd *cobra.Command) {
	if goruntime.GOOS != "darwin" {
		fmt.Fprintln(cmd.OutOrStdout(), "launchd plist: skipped (non-macOS)")
		return
	}
	plist, err := launchdPlistPath()
	if err != nil {
		fmt.Fprintf(cmd.OutOrStdout(), "launchd plist: error: %v\n", err)
		return
	}
	if _, err := os.Stat(plist); err == nil {
		fmt.Fprintf(cmd.OutOrStdout(), "launchd plist: installed at %s\n", plist)
	} else {
		fmt.Fprintln(cmd.OutOrStdout(), "launchd plist: NOT installed")
	}
}

// printWriterLockStatus surfaces ~/.valuz-oss/.single-writer.lock so the
// user can see which backend process currently holds the single-writer lock.
func printWriterLockStatus(cmd *cobra.Command) {
	home, err := os.UserHomeDir()
	if err != nil {
		fmt.Fprintf(cmd.OutOrStdout(), "writer lock file: error: %v\n", err)
		return
	}
	lockPath := filepath.Join(home, ".valuz-oss", ".single-writer.lock")
	info, err := os.Stat(lockPath)
	if err != nil {
		fmt.Fprintln(cmd.OutOrStdout(), "writer lock file: not present (no recent backend)")
		return
	}
	pid := "?"
	if data, err := os.ReadFile(lockPath); err == nil {
		pid = strings.TrimSpace(string(data))
		if pid == "" {
			pid = "?"
		}
	}
	fmt.Fprintf(cmd.OutOrStdout(), "writer lock file: %s (size=%d, last PID = %s)\n", lockPath, info.Size(), pid)
}

// printBackendProbe probes the resolved backend URL (the same resolution
// every other command uses — --backend-url / env / profile / discovery).
func printBackendProbe(cmd *cobra.Command, opts *RootOptions) {
	url := opts.BackendURL + "/v1/system/status"
	fmt.Fprintf(cmd.OutOrStdout(), "backend probe: %s …\n", url)
	client := &http.Client{Timeout: 2 * time.Second}
	resp, err := client.Get(url)
	if err != nil {
		fmt.Fprintf(cmd.OutOrStdout(), "  → unreachable (%s)\n", classifyHTTPErr(err))
		return
	}
	defer resp.Body.Close()
	tag := "OK"
	if resp.StatusCode >= 400 {
		tag = "FAIL"
	}
	fmt.Fprintf(cmd.OutOrStdout(), "  → HTTP %d (%s)\n", resp.StatusCode, tag)
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

func printCheck(cmd *cobra.Command, ok bool, body string) {
	tag := "[ok ]"
	if !ok {
		tag = "[MISS]"
	}
	fmt.Fprintf(cmd.OutOrStdout(), "  %s %s\n", tag, body)
}

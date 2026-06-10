package cmd

import (
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/runtime"
)

const frontendPort = 1420

// backendPort returns the expected backend port for the current mode.
// Order: VALUZ_BACKEND_PORT env > runtime.Discover() (dev:8000, bundle:19100).
func backendPort() int {
	if v := os.Getenv("VALUZ_BACKEND_PORT"); v != "" {
		if p, err := strconv.Atoi(v); err == nil {
			return p
		}
	}
	if paths, err := runtime.Discover(); err == nil && paths.BackendPort > 0 {
		return paths.BackendPort
	}
	return 8000
}

func newStatusCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "status",
		Short: "Show runtime service status",
		RunE: func(_ *cobra.Command, _ []string) error {
			bp := backendPort()
			fmt.Printf("backend  :%-5d  %-22s  pid=%s\n",
				bp, checkHTTP(bp, "/v1/projects"), listeningPID(bp))
			fmt.Printf("frontend :%-5d  %-22s  pid=%s\n",
				frontendPort, "(no probe)", listeningPID(frontendPort))
			return nil
		},
	}
}

func checkHTTP(port int, path string) string {
	client := &http.Client{Timeout: 2 * time.Second}
	resp, err := client.Get(fmt.Sprintf("http://127.0.0.1:%d%s", port, path))
	if err != nil {
		return fmt.Sprintf("down (%s)", classifyErr(err))
	}
	defer resp.Body.Close()
	return fmt.Sprintf("HTTP %d", resp.StatusCode)
}

func classifyErr(err error) string {
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

func listeningPID(port int) string {
	out, err := exec.Command(
		"lsof", "-iTCP:"+strconv.Itoa(port), "-sTCP:LISTEN", "-nP", "-t",
	).Output()
	if err != nil {
		return "-"
	}
	pids := strings.Fields(strings.TrimSpace(string(out)))
	if len(pids) == 0 {
		return "-"
	}
	return strings.Join(pids, ",")
}

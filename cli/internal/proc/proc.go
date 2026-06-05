// Package proc spawns and supervises the dev-mode subprocesses
// (backend, frontend) that “valuz start“ brings up.
//
// The package replaces the previous scripts/dev.sh launcher with native
// Go: every aspect — process spawning, log redirection, HTTP readiness
// probing, PID-file bookkeeping, and Ctrl+C signal propagation in
// foreground mode — is implemented here so the CLI has no shell-script
// dependency at runtime.
package proc

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"
)

// Spec describes one subprocess valuz should run.
type Spec struct {
	Name string // "backend" / "frontend" — used for log filename + PID record key
	Bin  string // executable, e.g. "uv" or "pnpm"
	Args []string
	Cwd  string
	Env  []string // extra "KEY=VAL" entries on top of os.Environ()

	// ReadyURL, when non-empty, is polled after Spawn returns. The
	// process is considered ready when the URL returns 2xx within
	// ReadyTimeout. Zero ReadyTimeout disables the probe.
	ReadyURL     string
	ReadyTimeout time.Duration
}

// Running holds the runtime handle of a spawned Spec.
type Running struct {
	Spec    Spec
	Cmd     *exec.Cmd
	LogFile string
}

// PidRecord is what we persist to .ai/dev/valuz.pid so valuz stop can
// kill exactly the processes valuz start launched.
type PidRecord struct {
	Backend  int `json:"backend,omitempty"`
	Frontend int `json:"frontend,omitempty"`
}

// LogPath returns the absolute log path for a given spec.
func LogPath(logDir, name string) string {
	return filepath.Join(logDir, name+".log")
}

// Spawn starts every spec as a child process. Output is teed to log
// files under logDir; in foreground mode the same lines are also
// streamed to the parent's stdout/stderr so the user can watch them.
//
// In foreground mode this function blocks until either all children
// exit or the parent receives SIGINT/SIGTERM; on signal it propagates
// SIGTERM down to the children and waits for them.
//
// In background mode it returns immediately after writing the PID
// record. Children are detached via Setsid so they survive the CLI exit.
//
// Returns a non-nil error when any readiness probe times out, even if
// the child process is still alive — callers can decide whether to
// keep the children running (default for local dev) or kill them. The
// returned []*Running is always populated so callers can report PIDs
// in error messages.
func Spawn(specs []Spec, logDir string, foreground bool) ([]*Running, error) {
	if err := os.MkdirAll(logDir, 0o755); err != nil {
		return nil, fmt.Errorf("create log dir: %w", err)
	}

	out := make([]*Running, 0, len(specs))
	for _, s := range specs {
		r, err := start(s, logDir, foreground)
		if err != nil {
			// Roll back any already-started children so we don't leak
			// half-launched state.
			killAll(out)
			return nil, fmt.Errorf("spawn %s: %w", s.Name, err)
		}
		out = append(out, r)
	}

	if err := writePidFile(logDir, out); err != nil {
		killAll(out)
		return nil, err
	}

	// Run readiness probes in parallel. Failures bubble up as the
	// function's error return; we do NOT kill the children — they may
	// be slow but recoverable, and `valuz stop` cleans them up if
	// the user gives up.
	probeErr := probe(out)

	if foreground {
		waitForeground(out)
		return out, probeErr
	}
	return out, probeErr
}

// PrecheckRunning returns the set of services that look alive per the
// recorded PID file. Callers use this to refuse a double-start while
// allowing partial starts (e.g. start frontend when backend was
// already started in a previous invocation).
func PrecheckRunning(logDir string, names []string) ([]string, error) {
	rec, err := ReadPidFile(logDir)
	if err != nil {
		return nil, err
	}
	var conflicts []string
	for _, name := range names {
		pid := recordedPid(rec, name)
		if pid > 0 && syscall.Kill(pid, 0) == nil {
			conflicts = append(conflicts, fmt.Sprintf("%s pid=%d", name, pid))
		}
	}
	return conflicts, nil
}

func recordedPid(rec PidRecord, name string) int {
	switch name {
	case "backend":
		return rec.Backend
	case "frontend":
		return rec.Frontend
	}
	return 0
}

func start(s Spec, logDir string, foreground bool) (*Running, error) {
	logPath := LogPath(logDir, s.Name)
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return nil, fmt.Errorf("open log file %s: %w", logPath, err)
	}
	_, _ = logFile.WriteString(fmt.Sprintf("\n--- valuz spawn %s at %s ---\n", s.Name, time.Now().Format(time.RFC3339)))

	cmd := exec.Command(s.Bin, s.Args...)
	cmd.Dir = s.Cwd
	cmd.Env = append(os.Environ(), s.Env...)

	if foreground {
		// Tee to both stdout/stderr and the log file so Ctrl+C surfaces
		// failures fast while the log keeps the full record.
		cmd.Stdout = io.MultiWriter(os.Stdout, logFile)
		cmd.Stderr = io.MultiWriter(os.Stderr, logFile)
		cmd.Stdin = os.Stdin
	} else {
		cmd.Stdout = logFile
		cmd.Stderr = logFile
		cmd.Stdin = nil
		// Detach from the controlling tty so the launcher survives the
		// invoking shell. setsid is the standard nohup-equivalent.
		cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	}

	if err := cmd.Start(); err != nil {
		_ = logFile.Close()
		return nil, err
	}
	return &Running{Spec: s, Cmd: cmd, LogFile: logPath}, nil
}

// probe polls each spec's ReadyURL until it responds or the per-spec
// timeout expires. Returns an aggregated error when one or more specs
// never became ready — useful so `valuz start` can exit non-zero in CI.
func probe(rs []*Running) error {
	var (
		wg     sync.WaitGroup
		mu     sync.Mutex
		failed []string
	)
	for _, r := range rs {
		if r.Spec.ReadyURL == "" || r.Spec.ReadyTimeout == 0 {
			continue
		}
		wg.Add(1)
		go func(r *Running) {
			defer wg.Done()
			deadline := time.Now().Add(r.Spec.ReadyTimeout)
			client := &http.Client{Timeout: 2 * time.Second}
			for time.Now().Before(deadline) {
				resp, err := client.Get(r.Spec.ReadyURL)
				if err == nil {
					_ = resp.Body.Close()
					if resp.StatusCode < 400 {
						fmt.Fprintf(os.Stderr, "[valuz] %s ready (HTTP %d)\n", r.Spec.Name, resp.StatusCode)
						return
					}
				}
				time.Sleep(time.Second)
			}
			fmt.Fprintf(os.Stderr, "[valuz] %s did not respond within %s — check %s\n",
				r.Spec.Name, r.Spec.ReadyTimeout, r.LogFile)
			mu.Lock()
			failed = append(failed, r.Spec.Name)
			mu.Unlock()
		}(r)
	}
	wg.Wait()
	if len(failed) == 0 {
		return nil
	}
	return fmt.Errorf("readiness probe failed: %s", strings.Join(failed, ", "))
}

func waitForeground(rs []*Running) {
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	exitCh := make(chan *Running, len(rs))
	for _, r := range rs {
		go func(r *Running) {
			_ = r.Cmd.Wait()
			exitCh <- r
		}(r)
	}

	select {
	case sig := <-sigCh:
		fmt.Fprintf(os.Stderr, "\n[valuz] received %s, shutting down…\n", sig)
		killAll(rs)
	case r := <-exitCh:
		// One child exited on its own — bring the others down too.
		fmt.Fprintf(os.Stderr, "[valuz] %s exited; stopping siblings\n", r.Spec.Name)
		killAll(rs)
	}

	// Drain any still-pending Wait()s with a hard timeout.
	timeout := time.After(8 * time.Second)
	pending := len(rs) - 1
	for pending > 0 {
		select {
		case <-exitCh:
			pending--
		case <-timeout:
			fmt.Fprintln(os.Stderr, "[valuz] some children did not exit within 8s")
			return
		}
	}
}

// killAll sends SIGTERM to every running child's process group (or PID
// when no group is set up), then SIGKILL after a 5s grace period.
func killAll(rs []*Running) {
	for _, r := range rs {
		signalProcess(r.Cmd, syscall.SIGTERM)
	}
	time.Sleep(5 * time.Second)
	for _, r := range rs {
		signalProcess(r.Cmd, syscall.SIGKILL)
	}
}

func signalProcess(cmd *exec.Cmd, sig syscall.Signal) {
	if cmd == nil || cmd.Process == nil {
		return
	}
	// If we set Setsid (background mode), the child is its own process
	// group leader; signal the group to take down the whole tree
	// (pnpm spawns vite which spawns electron — they all share the pgid).
	if cmd.SysProcAttr != nil && cmd.SysProcAttr.Setsid {
		_ = syscall.Kill(-cmd.Process.Pid, sig)
		return
	}
	_ = cmd.Process.Signal(sig)
}

const pidFilename = "valuz.pid"

func pidPath(logDir string) string {
	return filepath.Join(logDir, pidFilename)
}

// writePidFile records the PIDs of background children so “valuz stop“
// can find them later. Foreground mode also writes the file — that way
// “valuz status“ from another terminal can still inspect things — but
// removes it on a clean exit.
//
// The file is read-then-merged: starting just `frontend` after `backend`
// preserves the backend entry. Only the specs in rs are overwritten.
func writePidFile(logDir string, rs []*Running) error {
	rec, _ := ReadPidFile(logDir) // start from existing; ignore read errors (e.g. malformed → reset)
	for _, r := range rs {
		switch r.Spec.Name {
		case "backend":
			rec.Backend = r.Cmd.Process.Pid
		case "frontend":
			rec.Frontend = r.Cmd.Process.Pid
		}
	}
	data, err := json.MarshalIndent(rec, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(pidPath(logDir), data, 0o644); err != nil {
		return fmt.Errorf("write pid file: %w", err)
	}
	return nil
}

// ReadPidFile returns the recorded PIDs (or an empty record if missing).
func ReadPidFile(logDir string) (PidRecord, error) {
	var rec PidRecord
	data, err := os.ReadFile(pidPath(logDir))
	if errors.Is(err, os.ErrNotExist) {
		return rec, nil
	}
	if err != nil {
		return rec, err
	}
	if err := json.Unmarshal(data, &rec); err != nil {
		return rec, err
	}
	return rec, nil
}

// RemovePidFile deletes the PID record. Used after a clean “valuz stop“.
func RemovePidFile(logDir string) error {
	err := os.Remove(pidPath(logDir))
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	return err
}

// StopByPid sends sig to a recorded PID and its process group.
// Returns true if the PID was alive when we signalled it.
func StopByPid(pid int, sig syscall.Signal) bool {
	if pid <= 0 {
		return false
	}
	// Probe liveness with signal 0 first.
	if err := syscall.Kill(pid, 0); err != nil {
		return false
	}
	// Try the process group; fall back to PID alone.
	if err := syscall.Kill(-pid, sig); err != nil {
		_ = syscall.Kill(pid, sig)
	}
	return true
}

// WaitFor polls until ctx is done or pid no longer exists.
func WaitFor(ctx context.Context, pid int) {
	for {
		select {
		case <-ctx.Done():
			return
		default:
			if syscall.Kill(pid, 0) != nil {
				return
			}
			time.Sleep(200 * time.Millisecond)
		}
	}
}

// FormatTail returns the trailing lines of a log file, used by “valuz
// status“ and on failed readiness probes. Implementation kept simple
// — for full streaming use “valuz logs“.
func FormatTail(path string, lines int) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	parts := strings.Split(strings.TrimRight(string(data), "\n"), "\n")
	if len(parts) > lines {
		parts = parts[len(parts)-lines:]
	}
	return strings.Join(parts, "\n")
}

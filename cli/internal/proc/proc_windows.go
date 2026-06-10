package proc

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"strconv"
	"syscall"
	"time"
)

func setupSignalNotify(ch chan<- os.Signal) {
	signal.Notify(ch, syscall.SIGINT, syscall.SIGTERM)
}

// PrecheckRunning returns the set of services that look alive per the
// recorded PID file.
func PrecheckRunning(logDir string, names []string) ([]string, error) {
	rec, err := ReadPidFile(logDir)
	if err != nil {
		return nil, err
	}
	var conflicts []string
	for _, name := range names {
		pid := recordedPid(rec, name)
		if pid > 0 && isProcessAlive(pid) {
			conflicts = append(conflicts, fmt.Sprintf("%s pid=%d", name, pid))
		}
	}
	return conflicts, nil
}

func setDetachAttr(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{
		CreationFlags: syscall.CREATE_NEW_PROCESS_GROUP,
	}
}

func terminateProcess(cmd *exec.Cmd) {
	if cmd == nil || cmd.Process == nil {
		return
	}
	_ = cmd.Process.Signal(syscall.SIGTERM)
}

func killProcess(cmd *exec.Cmd) {
	if cmd == nil || cmd.Process == nil {
		return
	}
	_ = cmd.Process.Kill()
}

// StopByPid terminates a process by PID using taskkill.
// sig is used as a hint: SIGKILL triggers taskkill /F (force), anything else
// is a graceful attempt.
func StopByPid(pid int, sig syscall.Signal) bool {
	if pid <= 0 || !isProcessAlive(pid) {
		return false
	}
	args := []string{"/PID", strconv.Itoa(pid)}
	if sig == syscall.SIGKILL {
		args = append([]string{"/F"}, args...)
	}
	return exec.Command("taskkill", args...).Run() == nil
}

// WaitFor polls until ctx is done or pid no longer exists.
func WaitFor(ctx context.Context, pid int) {
	for {
		select {
		case <-ctx.Done():
			return
		default:
			if !isProcessAlive(pid) {
				return
			}
			time.Sleep(200 * time.Millisecond)
		}
	}
}

func isProcessAlive(pid int) bool {
	proc, err := os.FindProcess(pid)
	if err != nil {
		return false
	}
	err = proc.Signal(syscall.Signal(0))
	return err == nil
}

package cmd

import (
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"syscall"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/proc"
)

func stopSignal(force bool) syscall.Signal {
	if force {
		return syscall.SIGKILL
	}
	return syscall.SIGTERM
}

func escalatePids(rec proc.PidRecord) {
	for _, pid := range []int{rec.Backend, rec.Frontend} {
		if pid > 0 && proc.StopByPid(pid, syscall.SIGKILL) {
			fmt.Printf("[valuz] force-killed pid=%d\n", pid)
		}
	}
}

func quitValuzApp() bool {
	// taskkill without /F sends WM_CLOSE to the process (graceful).
	err := exec.Command("taskkill", "/IM", "Valuz.exe").Run()
	if err != nil {
		return false
	}
	return true
}

func stopProcessByPattern(pattern string, force bool) bool {
	args := []string{"process", "where", fmt.Sprintf("commandline like '%%%s%%'", pattern), "get", "processid"}
	out, err := exec.Command("wmic", args...).Output()
	if err != nil {
		return false
	}
	killed := false
	for _, line := range strings.Split(string(out), "\n") {
		line = strings.TrimSpace(line)
		if pid, err := strconv.Atoi(line); err == nil && pid > 0 {
			killArgs := []string{"/PID", strconv.Itoa(pid)}
			if force {
				killArgs = append([]string{"/F"}, killArgs...)
			}
			if exec.Command("taskkill", killArgs...).Run() == nil {
				killed = true
			}
		}
	}
	return killed
}

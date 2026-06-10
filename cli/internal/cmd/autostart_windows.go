//go:build windows

package cmd

import (
	"fmt"
	"os"
	"os/exec"
)

const taskName = "ValuzBackend"

func installAutostartPlatform(exe string, port int, logDir string) error {
	if err := os.MkdirAll(logDir, 0o755); err != nil {
		return err
	}

	trigger := fmt.Sprintf("%s --host 127.0.0.1 --port %d", exe, port)
	out, err := exec.Command("schtasks", "/create", "/tn", taskName,
		"/tr", trigger, "/sc", "onlogon", "/rl", "limited", "/f").CombinedOutput()
	if err != nil {
		return fmt.Errorf("schtasks create failed: %s", trim(out))
	}
	fmt.Printf("installed Task Scheduler entry '%s' (port=%d, exe=%s)\n", taskName, port, exe)
	fmt.Println("backend will start automatically at next login.")
	return nil
}

func uninstallAutostartPlatform() error {
	out, err := exec.Command("schtasks", "/delete", "/tn", taskName, "/f").CombinedOutput()
	if err != nil {
		return fmt.Errorf("schtasks delete failed: %s", trim(out))
	}
	fmt.Printf("removed Task Scheduler entry '%s'\n", taskName)
	return nil
}

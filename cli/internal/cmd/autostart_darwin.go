//go:build darwin

package cmd

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
)

const launchdLabel = "io.valuz.oss"

func installAutostartPlatform(exe string, port int, logDir string) error {
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
	_ = exec.Command("launchctl", "unload", plistPath).Run()
	loadOut, err := exec.Command("launchctl", "load", plistPath).CombinedOutput()
	if err != nil {
		return fmt.Errorf("launchctl load failed: %s", trim(loadOut))
	}
	fmt.Printf("installed %s (port=%d, exe=%s)\n", plistPath, port, exe)
	fmt.Println("backend will start automatically at next login (and now).")
	return nil
}

func uninstallAutostartPlatform() error {
	plistPath, err := launchdPlistPath()
	if err != nil {
		return err
	}
	if _, err := os.Stat(plistPath); os.IsNotExist(err) {
		fmt.Println("nothing to uninstall (plist not found).")
		return nil
	}
	_ = exec.Command("launchctl", "unload", plistPath).Run()
	if err := os.Remove(plistPath); err != nil && !os.IsNotExist(err) {
		return err
	}
	fmt.Printf("removed %s\n", plistPath)
	return nil
}

func launchdPlistPath() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, "Library", "LaunchAgents", launchdLabel+".plist"), nil
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

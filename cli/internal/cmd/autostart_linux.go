//go:build linux

package cmd

import "errors"

func installAutostartPlatform(exe string, port int, logDir string) error {
	return errors.New("install-autostart on Linux is not yet implemented (systemd template pending)")
}

func uninstallAutostartPlatform() error {
	return errors.New("uninstall-autostart on Linux is not yet implemented (systemd template pending)")
}

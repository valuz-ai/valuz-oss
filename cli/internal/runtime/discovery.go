// Package runtime resolves where the CLI's sibling components live.
//
// Order of resolution per docs/STRUCTURE.md "Runtime Discovery":
//  1. Desktop bundle - packaged binaries beside the .app (the running CLI
//     is at "Valuz.app/Contents/Resources/bin/valuz")
//  2. Standalone install - beside the CLI binary (bin/ + libexec/ layout)
//  3. Development checkout - fall back to repo-relative source
//
// Each mode pins the right backend port (Electron's PERSONAL_PORTS.AGENT_SERVER
// = 19100 in production; the dev port = 8000) so callers - status probe,
// install-autostart default, HTTP client - agree on what's actually running.
package runtime

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
)

// Mode identifies the discovered runtime layout.
type Mode string

const (
	ModeDev        Mode = "dev"
	ModeBundle     Mode = "bundle"
	ModeStandalone Mode = "standalone"
)

// Default backend ports per mode. Keep in sync with
// frontend/packages/shared/src/constants/ports.ts (AGENT_SERVER = 19100)
// and backend/valuz_agent/main.py (--port default = 8000 for dev).
const (
	devBackendPort    = 8000
	bundleBackendPort = 19100
)

// Paths is the resolved set of paths used by every command.
type Paths struct {
	Mode        Mode
	BackendPort int    // canonical port the backend is expected to bind in this mode
	ServerExe   string // absolute path to valuz-server (bundle mode only; empty in dev)
	LibexecDir  string // absolute path to the libexec dir (bundle / standalone)

	// Dev-only fields. Empty in bundle / standalone modes.
	RepoRoot    string
	BackendDir  string
	FrontendDir string
	CliDir      string
	LogDir      string // .ai/dev under repo root in dev mode
}

// Discover returns the runtime paths for the current invocation.
//
// Detection ladder:
//  1. If the running CLI sits inside a packaged bundle (macOS .app or
//     Windows NSIS layout) -> ModeBundle
//  2. If VALUZ_REPO_ROOT is set, or the current cwd / the CLI's
//     parents look like the repo -> ModeDev
//  3. Otherwise -> error (ModeStandalone TODO)
func Discover() (*Paths, error) {
	exe, _ := os.Executable()
	if bundleRoot := detectBundleRoot(exe); bundleRoot != "" {
		return bundlePaths(bundleRoot)
	}

	if root := os.Getenv("VALUZ_REPO_ROOT"); root != "" {
		return devPaths(root)
	}
	if cwd, err := os.Getwd(); err == nil {
		if root := findRepoRoot(cwd); root != "" {
			return devPaths(root)
		}
	}
	if exe != "" {
		if root := findRepoRoot(filepath.Dir(exe)); root != "" {
			return devPaths(root)
		}
	}
	return nil, errors.New(
		"could not locate the Valuz repository root; " +
			"set VALUZ_REPO_ROOT or run from inside the checkout",
	)
}

// detectBundleRoot returns the absolute path of the resources directory
// when exe resolves inside a packaged install, or "" otherwise.
//
// macOS: <...>/Valuz.app/Contents/Resources/bin/valuz
// Windows (NSIS): <...>/resources/bin/valuz.exe
func detectBundleRoot(exe string) string {
	if exe == "" {
		return ""
	}
	resolved, err := filepath.EvalSymlinks(exe)
	if err == nil {
		exe = resolved
	}

	if runtime.GOOS == "darwin" {
		// Expected: <...>/Valuz.app/Contents/Resources/bin/valuz
		binDir := filepath.Dir(exe)
		resourcesDir := filepath.Dir(binDir)
		contentsDir := filepath.Dir(resourcesDir)
		appDir := filepath.Dir(contentsDir)
		if filepath.Base(binDir) != "bin" {
			return ""
		}
		if filepath.Base(resourcesDir) != "Resources" {
			return ""
		}
		if filepath.Base(contentsDir) != "Contents" {
			return ""
		}
		if !hasSuffix(appDir, ".app") {
			return ""
		}
		if !isFile(filepath.Join(resourcesDir, "libexec", "valuz-server")) {
			return ""
		}
		return resourcesDir
	}

	if runtime.GOOS == "windows" {
		// NSIS installs to: C:\...\Valuz\resources\bin\valuz.exe
		binDir := filepath.Dir(exe)
		resourcesDir := filepath.Dir(binDir)
		if filepath.Base(binDir) != "bin" {
			return ""
		}
		serverExe := filepath.Join(resourcesDir, "libexec", "valuz-server.exe")
		if !isFile(serverExe) {
			return ""
		}
		return resourcesDir
	}

	return ""
}

func hasSuffix(s, suffix string) bool {
	if len(s) < len(suffix) {
		return false
	}
	return s[len(s)-len(suffix):] == suffix
}

func bundlePaths(resourcesDir string) (*Paths, error) {
	libexec := filepath.Join(resourcesDir, "libexec")
	serverName := "valuz-server"
	if runtime.GOOS == "windows" {
		serverName = "valuz-server.exe"
	}
	exe := filepath.Join(libexec, serverName)
	if _, err := os.Stat(exe); err != nil {
		return nil, fmt.Errorf("bundled valuz-server not found at %s: %w", exe, err)
	}
	return &Paths{
		Mode:        ModeBundle,
		BackendPort: bundleBackendPort,
		ServerExe:   exe,
		LibexecDir:  libexec,
		LogDir:      filepath.Join(mustHome(), ".valuz", "app", "logs"),
	}, nil
}

func devPaths(root string) (*Paths, error) {
	abs, err := filepath.Abs(root)
	if err != nil {
		return nil, err
	}
	return &Paths{
		Mode:        ModeDev,
		BackendPort: devBackendPort,
		RepoRoot:    abs,
		BackendDir:  filepath.Join(abs, "backend"),
		FrontendDir: filepath.Join(abs, "frontend"),
		CliDir:      filepath.Join(abs, "cli"),
		LogDir:      filepath.Join(abs, ".ai", "dev"),
	}, nil
}

// findRepoRoot walks up from start looking for the joint fingerprint
// (backend/valuz_agent + frontend/apps). Returns "" if not found.
func findRepoRoot(start string) string {
	dir := start
	for {
		hasBackend := isDir(filepath.Join(dir, "backend", "valuz_agent"))
		hasFrontend := isDir(filepath.Join(dir, "frontend", "apps"))
		if hasBackend && hasFrontend {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return ""
		}
		dir = parent
	}
}

func isDir(p string) bool {
	info, err := os.Stat(p)
	return err == nil && info.IsDir()
}

func isFile(p string) bool {
	info, err := os.Stat(p)
	return err == nil && !info.IsDir()
}

func mustHome() string {
	h, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return h
}

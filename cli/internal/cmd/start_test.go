package cmd

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// devDataEnv mirrors scripts/dev.sh: default the dev backend onto
// ~/.valuz-oss-dev so `valuz start` can never run a source backend on the
// packaged app's ~/.valuz-oss.
func TestDevDataEnvDefaultsDataDirAndLogFile(t *testing.T) {
	t.Setenv("VALUZ_DATA_DIR", "")
	t.Setenv("VALUZ_LOG_FILE_PATH", "")
	os.Unsetenv("VALUZ_DATA_DIR")
	os.Unsetenv("VALUZ_LOG_FILE_PATH")

	home, err := os.UserHomeDir()
	if err != nil {
		t.Skip("no home dir in test environment")
	}
	want := filepath.Join(home, ".valuz-oss-dev")

	env := devDataEnv()
	if len(env) != 2 {
		t.Fatalf("expected 2 entries, got %v", env)
	}
	if env[0] != "VALUZ_DATA_DIR="+want {
		t.Errorf("data dir entry = %q, want %q", env[0], "VALUZ_DATA_DIR="+want)
	}
	if env[1] != "VALUZ_LOG_FILE_PATH="+filepath.Join(want, "logs", "backend.log") {
		t.Errorf("log file entry = %q, want under %q", env[1], want)
	}
}

func TestDevDataEnvRespectsExplicitDataDir(t *testing.T) {
	t.Setenv("VALUZ_DATA_DIR", "/custom/root")
	t.Setenv("VALUZ_LOG_FILE_PATH", "")
	os.Unsetenv("VALUZ_LOG_FILE_PATH")

	env := devDataEnv()
	if len(env) != 1 || !strings.HasPrefix(env[0], "VALUZ_LOG_FILE_PATH=") {
		t.Fatalf("expected only a derived VALUZ_LOG_FILE_PATH entry, got %v", env)
	}
	if env[0] != "VALUZ_LOG_FILE_PATH="+filepath.Join("/custom/root", "logs", "backend.log") {
		t.Errorf("log file entry = %q", env[0])
	}
}

func TestDevDataEnvNoOpWhenBothSet(t *testing.T) {
	t.Setenv("VALUZ_DATA_DIR", "/custom/root")
	t.Setenv("VALUZ_LOG_FILE_PATH", "/custom/logs/app.log")

	if env := devDataEnv(); len(env) != 0 {
		t.Fatalf("expected no overrides, got %v", env)
	}
}

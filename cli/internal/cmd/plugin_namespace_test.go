package cmd

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestPluginHelpSeparatesFamilies(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	out, _, err := runCmd(t, Root(), "plugin", "--help")
	if err != nil {
		t.Fatal(err)
	}
	for _, family := range []string{"agent", "app", "builtin", "dsh"} {
		if !strings.Contains(out, "  "+family+" ") {
			t.Fatalf("missing family %q in help:\n%s", family, out)
		}
	}
	for _, legacy := range []string{"  install ", "  publish ", "  validate "} {
		if strings.Contains(out, legacy) {
			t.Fatalf("legacy command advertised in help:\n%s", out)
		}
	}
	out, _, err = runCmd(t, Root(), "--help")
	if err != nil || strings.Contains(out, "  ext ") {
		t.Fatalf("ext must be absent from root help: %v\n%s", err, out)
	}
}

func TestRemovedPluginCommandsFailWithoutCallingBackend(t *testing.T) {
	f := newFakePluginBackend(t)
	defer f.Close()
	isolatePluginEnv(t, f.URL)
	for _, args := range [][]string{
		{"ext", "list"}, {"ext", "dsh", "list"},
		{"plugin", "list"}, {"plugin", "install", "./plugin.zip"},
		{"plugin", "publish", "./plugin.zip"}, {"plugin", "validate", "."},
	} {
		t.Run(strings.Join(args, " "), func(t *testing.T) {
			out, _, err := runPluginCmd(t, nil, "", args...)
			if err == nil || !strings.Contains(err.Error(), "unknown command") {
				t.Fatalf("removed command should be unknown: args=%v err=%v stdout=%q", args, err, out)
			}
			if calls := f.calls(); len(calls) != 0 {
				t.Fatalf("removed command contacted backend: %+v", calls)
			}
		})
	}
}

func TestCanonicalPluginCommandsHaveNoDeprecationNotice(t *testing.T) {
	useFakeBuiltinPluginBackend(t, "applied")
	for _, args := range [][]string{
		{"plugin", "builtin", "list", "-o", "json"},
		{"plugin", "dsh", "list", "-o", "json"},
	} {
		out, stderr, err := runCmd(t, Root(), args...)
		if err != nil || !json.Valid([]byte(out)) || strings.Contains(stderr, "Deprecated") {
			t.Fatalf("canonical %v: err=%v stdout=%q stderr=%q", args, err, out, stderr)
		}
	}
}

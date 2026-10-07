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
		t.Fatalf("ext must be hidden from root help: %v\n%s", err, out)
	}
}

func TestLegacyAppCommandPreservesJSON(t *testing.T) {
	f := newFakePluginBackend(t)
	defer f.Close()
	isolatePluginEnv(t, f.URL)
	out, stderr, err := runPluginCmd(t, nil, "", "plugin", "list", "-o", "json")
	if err != nil {
		t.Fatal(err)
	}
	if !json.Valid([]byte(out)) || !strings.Contains(stderr, "valuz plugin app list") {
		t.Fatalf("JSON stdout and migration warning required: stdout=%q stderr=%q", out, stderr)
	}
	if reqs := f.calls(); len(reqs) != 1 || reqs[0].Path != appPluginAPI {
		t.Fatalf("wrong legacy route: %+v", reqs)
	}
}

func TestLegacyExtCommandPreservesJSON(t *testing.T) {
	useFakeBuiltinPluginBackend(t, "applied")
	for _, tc := range []struct {
		args []string
		next string
	}{
		{[]string{"ext", "list", "-o", "json"}, "valuz plugin builtin list"},
		{[]string{"ext", "dsh", "list", "-o", "json"}, "valuz plugin dsh list"},
	} {
		out, stderr, err := runCmd(t, Root(), tc.args...)
		if err != nil || !json.Valid([]byte(out)) || !strings.Contains(stderr, tc.next) {
			t.Fatalf("legacy %v: err=%v stdout=%q stderr=%q", tc.args, err, out, stderr)
		}
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

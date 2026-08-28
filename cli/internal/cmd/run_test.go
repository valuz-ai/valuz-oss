package cmd

import (
	"bytes"
	"os"
	"strings"
	"testing"

	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/output"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/runner"
)

func TestRunExitError(t *testing.T) {
	base := func(status, signal string) *runner.Result {
		return &runner.Result{Status: status, Signal: signal}
	}

	cases := []struct {
		name   string
		res    *runner.Result
		want   int
		wantOK bool
	}{
		{"completed exits 0", base(output.StatusCompleted, ""), 0, true},
		{"error exits 3", base(output.StatusError, ""), 3, false},
		{"timeout exits 2", base(output.StatusTimeout, ""), 2, false},
		{"action_required exits 7", base(output.StatusActionRequired, ""), 7, false},
		{"SIGINT exits 130", base(output.StatusInterrupted, "SIGINT"), 130, false},
		{"SIGTERM exits 143", base(output.StatusInterrupted, "SIGTERM"), 143, false},
		{"auth_error exits 6", base(output.StatusAuthError, ""), 6, false},
		{"internal_error exits 5", base(output.StatusInternalError, ""), 5, false},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := runExitError(tc.res)
			if tc.wantOK {
				if err != nil {
					t.Fatalf("want nil, got %v", err)
				}
				return
			}
			ece, ok := err.(*errs.ExitCodeError)
			if !ok {
				t.Fatalf("want ExitCodeError, got %T: %v", err, err)
			}
			if ece.Code != tc.want {
				t.Fatalf("exit code = %d, want %d", ece.Code, tc.want)
			}
		})
	}
}

func TestResolvePromptExclusivity(t *testing.T) {
	dir := t.TempDir()
	file := dir + "/prompt.txt"
	if err := writeFile(file, "fix the tests"); err != nil {
		t.Fatalf("write: %v", err)
	}

	cmd := Root()
	stdin := strings.NewReader("from stdin")
	cmd.SetIn(stdin)

	// exactly one source
	if got, err := resolvePrompt("hello", "", false, cmd); err != nil || got != "hello" {
		t.Fatalf("prompt: got %q err %v", got, err)
	}
	if got, err := resolvePrompt("", file, false, cmd); err != nil || got != "fix the tests" {
		t.Fatalf("prompt-file: got %q err %v", got, err)
	}
	if got, err := resolvePrompt("", "", true, cmd); err != nil || got != "from stdin" {
		t.Fatalf("prompt-stdin: got %q err %v", got, err)
	}

	// exclusivity violations
	for _, args := range [][3]any{
		{"a", file, false},
		{"a", "", true},
		{"", file, true},
	} {
		p, f, s := args[0].(string), args[1].(string), args[2].(bool)
		if _, err := resolvePrompt(p, f, s, cmd); err == nil {
			t.Fatalf("expected exclusivity error for %v", args)
		}
	}

	// empty file / stdin
	empty := dir + "/empty.txt"
	if err := writeFile(empty, ""); err != nil {
		t.Fatalf("write: %v", err)
	}
	if _, err := resolvePrompt("", empty, false, cmd); err == nil {
		t.Fatal("empty prompt-file must error")
	}
	cmd.SetIn(strings.NewReader(""))
	if _, err := resolvePrompt("", "", true, cmd); err == nil {
		t.Fatal("empty stdin must error")
	}

	// no source at all
	if _, err := resolvePrompt("", "", false, cmd); err == nil {
		t.Fatal("missing prompt must error")
	}
}

func TestExecuteReturnsExitCode(t *testing.T) {
	var stdout, stderr bytes.Buffer
	// `version` with an unsupported output is a KindUsage error → exit 1.
	code := Execute([]string{"version", "--output", "nope"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("exit = %d, want 1 (usage error)", code)
	}
	if stderr.Len() == 0 {
		t.Fatal("usage error must render on stderr")
	}

	// unknown command → cobra usage path; exit code must be non-zero.
	stdout.Reset()
	stderr.Reset()
	code = Execute([]string{"no-such-command"}, &stdout, &stderr)
	if code == 0 {
		t.Fatal("unknown command must exit non-zero")
	}
}

func writeFile(path, content string) error {
	return os.WriteFile(path, []byte(content), 0o600)
}

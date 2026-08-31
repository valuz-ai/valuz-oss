package errors

import (
	"errors"
	"fmt"
	"strings"
	"testing"
)

func TestExitCodes(t *testing.T) {
	cases := []struct {
		kind Kind
		want int
	}{
		{KindUsage, 1},
		{KindTimeout, 2},
		{KindAgent, 3},
		{KindUnreachable, 4},
		{KindInternal, 5},
		{KindAuth, 6},
		{KindActionRequired, 7},
	}
	for _, tc := range cases {
		if got := tc.kind.ExitCode(); got != tc.want {
			t.Fatalf("%s exit = %d, want %d", tc.kind, got, tc.want)
		}
	}
}

func TestKindOfUntaggedErrorDefaultsInternal(t *testing.T) {
	if got := KindOf(errors.New("plain")); got != KindInternal {
		t.Fatalf("untagged error kind = %s, want internal", got)
	}
	if got := KindOf(New(KindAuth, "nope")); got != KindAuth {
		t.Fatalf("typed error kind = %s, want auth", got)
	}
}

func TestRedact(t *testing.T) {
	cases := []struct {
		in   string
		want string
	}{
		{"Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc", "no eyJ must survive"},
		{"token=sk-abc123tokenvalue", "token=[REDACTED]"},
		{"no secrets here", "no secrets here"},
		{"the bearer certificates were redeemed", "the bearer certificates were redeemed"},
		{"token=", "token="},
		{"api_key: 1234567890abcdef", "api_key: [REDACTED]"},
		// Review-verified bypasses: opaque tokens after Bearer, tabs,
		// base64, bare JWT with a short tail.
		{"Authorization: Bearer sk-9f8a7s6d5f4a3s2d1f", "Authorization: [REDACTED]"},
		{"Authorization: Bearer\tTOPSECRETTOKEN123456", "Authorization: [REDACTED]"},
		{"Authorization: Bearer MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=", "Authorization: [REDACTED]"},
		{"token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc", "token [REDACTED]"},
		{"Bearer xyzzy-secret-token-0001", "[REDACTED]"},
	}
	for _, tc := range cases {
		got := Redact(tc.in)
		if tc.want == "no eyJ must survive" {
			if got == tc.in {
				t.Fatalf("Redact(%q) unchanged, want redaction", tc.in)
			}
			continue
		}
		if got != tc.want {
			t.Fatalf("Redact(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestRendererDebugAndHuman(t *testing.T) {
	root := Wrap(KindAuth, fmt.Errorf("jwt verify: token=eyJhbGciOiJIUzI1NiJ9.abc"),
		"backend rejected credentials")

	human := (&Renderer{Debug: false}).Render(root)
	if strings.Contains(human, "jwt verify") {
		t.Fatalf("human render leaked cause: %q", human)
	}
	if strings.Contains(human, "eyJ") {
		t.Fatalf("human render leaked token: %q", human)
	}

	debug := (&Renderer{Debug: true}).Render(root)
	if !strings.Contains(debug, "caused by:") {
		t.Fatalf("debug render missing cause chain: %q", debug)
	}
	if strings.Contains(debug, "eyJ") {
		t.Fatalf("debug render leaked token: %q", debug)
	}
}

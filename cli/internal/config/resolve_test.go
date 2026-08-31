package config

import (
	"os"
	"path/filepath"
	"testing"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/runtime"
)

func TestResolverBackendURLPrecedence(t *testing.T) {
	// discovery stub producing a port
	discoverPort := 0
	discover := func() (*runtime.Paths, error) {
		return &runtime.Paths{BackendPort: discoverPort}, nil
	}

	cases := []struct {
		name    string
		flagVal string
		envVal  string
		profile *Profile
		port    int
		want    string
	}{
		{"flag wins", "http://flag:1", "http://env:2", &Profile{BackendURL: "http://prof:3"}, 0, "http://flag:1"},
		{"env wins over profile", "", "http://env:2", &Profile{BackendURL: "http://prof:3"}, 0, "http://env:2"},
		{"profile wins over discovery", "", "", &Profile{BackendURL: "http://prof:3"}, 4, "http://prof:3"},
		{"discovery wins over default", "", "", nil, 19100, "http://127.0.0.1:19100"},
		{"default fallback", "", "", nil, 0, "http://127.0.0.1:8000"},
		{"trailing slash trimmed", "http://flag:1/", "", nil, 0, "http://flag:1"},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			discoverPort = tc.port
			r := NewResolver(tc.profile)
			r.Discovery = discover
			r.LookupEnv = func(key string) (string, bool) {
				if key == "VALUZ_BACKEND_BASE_URL" && tc.envVal != "" {
					return tc.envVal, true
				}
				return "", false
			}
			got, err := r.BackendURL(tc.flagVal)
			if err != nil {
				t.Fatalf("BackendURL: %v", err)
			}
			if got != tc.want {
				t.Fatalf("got %q want %q", got, tc.want)
			}
		})
	}
}

func TestResolverStringPrecedence(t *testing.T) {
	r := NewResolver(&Profile{Defaults: map[string]string{"RUN_TIMEOUT": "60"}})
	r.LookupEnv = func(key string) (string, bool) {
		if key == "VALUZ_RUN_TIMEOUT" {
			return "120", true
		}
		return "", false
	}

	if got := r.String("RUN_TIMEOUT", "", "0"); got != "120" {
		t.Fatalf("env precedence: got %q want 120", got)
	}
	if got := r.String("RUN_TIMEOUT", "90", "0"); got != "90" {
		t.Fatalf("flag precedence: got %q want 90", got)
	}
	if got := r.String("UNSET_KEY", "", "0"); got != "0" {
		t.Fatalf("fallback: got %q want 0", got)
	}
}

func TestProfileStoreRoundTrip(t *testing.T) {
	dir := t.TempDir()
	store := NewProfileStore(dir)

	p := &Profile{Name: "eval", BackendURL: "http://127.0.0.1:9000", Defaults: map[string]string{"RUN_TIMEOUT": "300"}}
	if err := store.Save(p); err != nil {
		t.Fatalf("Save: %v", err)
	}

	got, err := store.Load("eval")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if got.Name != "eval" || got.BackendURL != p.BackendURL || got.Default("RUN_TIMEOUT") != "300" {
		t.Fatalf("round trip mismatch: %+v", got)
	}

	info, err := os.Stat(filepath.Join(dir, "eval.json"))
	if err != nil {
		t.Fatalf("stat: %v", err)
	}
	if perm := info.Mode().Perm(); perm != 0o600 {
		t.Fatalf("profile perm = %o, want 600", perm)
	}

	missing, err := store.Load("nope")
	if err != nil || missing.Name != "nope" || missing.BackendURL != "" {
		t.Fatalf("missing profile should load empty: %+v err=%v", missing, err)
	}
}

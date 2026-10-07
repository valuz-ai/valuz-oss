package cmd

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

// fakeExtBackend serves /v1/extensions/backend and the dsh pluginManager proxy,
// recording every dsh remote call (method + args) it receives.
type fakeExtBackend struct {
	*httptest.Server
	mu      sync.Mutex
	calls   []string
	toggled map[string]bool
}

func newFakeExtBackend(t *testing.T, installApplication string) *fakeExtBackend {
	t.Helper()
	f := &fakeExtBackend{toggled: map[string]bool{}}
	mux := http.NewServeMux()
	mux.HandleFunc("/v1/extensions/backend", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, map[string]any{
			"composed": true,
			"editable": true,
			"plugins": []map[string]any{
				{"id": "commercial.identity", "status": "active", "required": true, "desiredEnabled": true},
				{"id": "commercial.sites", "status": "active", "required": false, "desiredEnabled": false},
				{"id": "commercial.labs", "status": "failed", "required": false, "desiredEnabled": true,
					"error": "need 'billing' is unavailable"},
			},
		})
	})
	mux.HandleFunc("/v1/extensions/backend/commercial.sites/enabled", func(w http.ResponseWriter, r *http.Request) {
		var body map[string]bool
		_ = json.NewDecoder(r.Body).Decode(&body)
		f.mu.Lock()
		f.toggled["commercial.sites"] = body["enabled"]
		f.mu.Unlock()
		writeJSON(t, w, map[string]string{"application": "restart-required"})
	})
	mux.HandleFunc("/v1/extensions/backend/commercial.identity/enabled", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		_ = json.NewEncoder(w).Encode(map[string]string{"detail": "'commercial.identity' is required"})
	})
	mux.HandleFunc("/v1/dsh/plugins/status", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, map[string]any{
			"available": true, "running": true, "profile": "valuz",
			"managed_bundles": []string{"@deepseek-ai/dsh-base", "valuz-dsh-bundle"},
		})
	})
	mux.HandleFunc("/v1/dsh/plugins/remote/", func(w http.ResponseWriter, r *http.Request) {
		method := strings.TrimPrefix(r.URL.Path, "/v1/dsh/plugins/remote/")
		var body struct {
			Args map[string]any `json:"args"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		raw, _ := json.Marshal(body.Args)
		f.mu.Lock()
		f.calls = append(f.calls, method+" "+string(raw))
		f.mu.Unlock()
		switch method {
		case "listBundles":
			writeJSON(t, w, map[string]any{"value": []map[string]any{
				{"name": "@deepseek-ai/dsh-base", "version": "0.2.1-alpha.1", "enabled": true, "removable": false},
				{"name": "@deepseek-ai/dsh-headless", "version": "0.2.1-alpha.1", "enabled": false, "removable": false},
				{"name": "dsh-hello-tool", "version": "1.0.0", "enabled": true, "removable": true},
			}})
		case "inspect":
			writeJSON(t, w, map[string]any{"value": map[string]any{
				"status": "accepted", "kind": "registry", "name": "dsh-hello-tool", "version": "1.0.0", "bundle": true,
			}})
		case "installBundle":
			change := map[string]any{"stage": "install", "target": "dsh-hello-tool", "application": installApplication}
			if installApplication == "failed" {
				change["error"] = map[string]any{"code": "install-failed", "diagnostic": "registry unreachable"}
			}
			writeJSON(t, w, map[string]any{"value": change})
		default:
			writeJSON(t, w, map[string]any{"value": map[string]any{
				"stage": "enable", "target": body.Args["name"], "application": "restart-required",
			}})
		}
	})
	f.Server = httptest.NewServer(mux)
	return f
}

func (f *fakeExtBackend) remoteCalls() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.calls...)
}

func useFakeExtBackend(t *testing.T, installApplication string) *fakeExtBackend {
	t.Helper()
	f := newFakeExtBackend(t, installApplication)
	t.Cleanup(f.Close)
	t.Setenv("HOME", t.TempDir())
	t.Setenv("VALUZ_BACKEND_BASE_URL", f.URL)
	return f
}

func TestExtListShowsStatusAndPendingRestart(t *testing.T) {
	useFakeExtBackend(t, "applied")
	out, _, err := runCmd(t, Root(), "plugin", "builtin", "list")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	for _, want := range []string{
		"commercial.identity", "required",
		"commercial.sites", "disable on restart",
		"commercial.labs", "error: need 'billing' is unavailable",
		"valuz restart",
	} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q in:\n%s", want, out)
		}
	}
}

func TestExtListJSONIsTheBackendShape(t *testing.T) {
	useFakeExtBackend(t, "applied")
	out, _, err := runCmd(t, Root(), "plugin", "builtin", "list", "-o", "json")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	var resp backendExtensions
	if err := json.Unmarshal([]byte(out), &resp); err != nil {
		t.Fatalf("not JSON: %v\n%s", err, out)
	}
	if !resp.Composed || len(resp.Plugins) != 3 || resp.Plugins[1].DesiredEnabled {
		t.Fatalf("unexpected: %+v", resp)
	}
}

func TestExtEnableRecordsTheDesire(t *testing.T) {
	f := useFakeExtBackend(t, "applied")
	out, _, err := runCmd(t, Root(), "plugin", "builtin", "enable", "commercial.sites")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if !strings.Contains(out, "restart-required") || !f.toggled["commercial.sites"] {
		t.Fatalf("toggle not sent / reported: %q %v", out, f.toggled)
	}
}

func TestExtDisableRequiredSurfacesTheServerDetail(t *testing.T) {
	useFakeExtBackend(t, "applied")
	_, _, err := runCmd(t, Root(), "plugin", "builtin", "disable", "commercial.identity")
	if err == nil || !strings.Contains(err.Error(), "is required") {
		t.Fatalf("want the 409 detail, got %v", err)
	}
}

func TestExtDshListGoesThroughPluginManager(t *testing.T) {
	f := useFakeExtBackend(t, "applied")
	out, _, err := runCmd(t, Root(), "plugin", "dsh", "list")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	lines := map[string]string{}
	for _, line := range strings.Split(strings.TrimSpace(out), "\n") {
		lines[strings.Fields(line)[0]] = line
	}
	if !strings.HasSuffix(lines["@deepseek-ai/dsh-base"], "managed by Valuz") ||
		!strings.HasSuffix(lines["@deepseek-ai/dsh-headless"], "ships with dsh") ||
		!strings.HasSuffix(lines["dsh-hello-tool"], "enabled") {
		t.Fatalf("unexpected:\n%s", out)
	}
	if calls := f.remoteCalls(); len(calls) != 1 || calls[0] != "listBundles {}" {
		t.Fatalf("calls = %v", calls)
	}
}

func TestExtDshAddInspectsAndStopsWithoutYes(t *testing.T) {
	f := useFakeExtBackend(t, "applied")
	out, _, err := runCmd(t, Root(), "plugin", "dsh", "add", "dsh-hello-tool")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if !strings.Contains(out, "--yes") {
		t.Fatalf("expected the confirmation hint:\n%s", out)
	}
	for _, c := range f.remoteCalls() {
		if strings.HasPrefix(c, "installBundle") {
			t.Fatalf("installed without --yes: %v", f.remoteCalls())
		}
	}
}

func TestExtDshAddInstallsTheInspectedSpec(t *testing.T) {
	f := useFakeExtBackend(t, "restart-required")
	out, _, err := runCmd(t, Root(), "plugin", "dsh", "add", "dsh-hello-tool", "--yes")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	calls := f.remoteCalls()
	want := []string{
		`inspect {"spec":"dsh-hello-tool"}`,
		`installBundle {"options":{"enabled":true},"spec":"dsh-hello-tool"}`,
	}
	if strings.Join(calls, "\n") != strings.Join(want, "\n") {
		t.Fatalf("calls = %v", calls)
	}
	if !strings.Contains(out, "install dsh-hello-tool: restart-required") {
		t.Fatalf("unexpected:\n%s", out)
	}
}

func TestExtDshFailedInstallExitsNonZero(t *testing.T) {
	useFakeExtBackend(t, "failed")
	_, _, err := runCmd(t, Root(), "plugin", "dsh", "add", "dsh-hello-tool", "--yes")
	if err == nil || !strings.Contains(err.Error(), "registry unreachable") {
		t.Fatalf("want the dsh diagnostic, got %v", err)
	}
}

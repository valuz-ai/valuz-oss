package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/pluginpkg"
)

// recordedRequest is one request the fake servers saw.
type recordedRequest struct {
	Method string
	Path   string
	Query  string
	Auth   string
	Body   map[string]any
}

// fakePluginBackend serves the local /v1/extensions/third-party API (task
// card 04 §D) and records every request.
type fakePluginBackend struct {
	*httptest.Server
	mu       sync.Mutex
	requests []recordedRequest
	// inspectErrors are returned by POST /inspect.
	inspectErrors []any
	// installStatus/installBody override POST /install's reply.
	installStatus int
	installBody   any
	logCalls      int
}

func samplePluginItem() map[string]any {
	return map[string]any{
		"id":             "acme.hello",
		"version":        "1.2.0",
		"name":           map[string]any{"en-US": "Hello"},
		"publisher":      map[string]any{"name": "Acme"},
		"source":         map[string]any{"kind": "dev", "path": "/src/acme-hello"},
		"status":         "requires-unmet",
		"status_reason":  "needs edition:finance",
		"enabled":        true,
		"permissions":    []string{"storage", "notifications"},
		"requires":       []string{"edition:finance"},
		"unmet_requires": []string{"edition:finance"},
		"entry_url":      "/v1/ext-assets/acme.hello/3/frontend/index.js",
		"automations":    []map[string]any{{"name": "daily"}},
		"revision":       3,
		"dev_path":       "/src/acme-hello",
		"sha256":         "",
		"installed_at":   "2026-10-07T10:00:00Z",
	}
}

func newFakePluginBackend(t *testing.T) *fakePluginBackend {
	t.Helper()
	f := &fakePluginBackend{}
	mux := http.NewServeMux()
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		raw, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(raw, &body)
		f.mu.Lock()
		f.requests = append(f.requests, recordedRequest{
			Method: r.Method, Path: r.URL.Path, Query: r.URL.RawQuery, Auth: r.Header.Get("Authorization"), Body: body,
		})
		f.mu.Unlock()

		item := samplePluginItem()
		const base = "/v1/extensions/third-party"
		switch {
		case r.Method == http.MethodGet && r.URL.Path == base:
			writeJSON(t, w, map[string]any{
				"api_version": "1.0.0", "safe_mode": true, "safe_mode_reason": "renderer crashed twice",
				"generation": 7, "plugins": []any{item},
			})
		case r.Method == http.MethodPost && r.URL.Path == base+"/inspect":
			writeJSON(t, w, map[string]any{
				"manifest": map[string]any{
					"id": "acme.hello", "version": "1.2.0", "name": map[string]any{"en-US": "Hello"},
					"publisher": map[string]any{"name": "Acme"},
				},
				"sha256":            "ab12cd34",
				"size":              2048,
				"permissions":       []string{"storage", "notifications"},
				"requires":          []string{"edition:finance"},
				"unmet_requires":    []string{"edition:finance"},
				"errors":            f.inspectErrors,
				"warnings":          []any{"icon: \"icon.png\" does not exist in the package"},
				"has_backend":       false,
				"existing":          map[string]any{"version": "1.1.0"},
				"added_permissions": []string{"notifications"},
			})
		case r.Method == http.MethodPost && r.URL.Path == base+"/install":
			if f.installStatus != 0 {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(f.installStatus)
				_ = json.NewEncoder(w).Encode(f.installBody)
				return
			}
			item["status"], item["status_reason"] = "enabled", nil
			writeJSON(t, w, map[string]any{"plugin": item, "updated_from": "1.1.0"})
		case r.Method == http.MethodPost && r.URL.Path == base+"/dev-link":
			item["status"], item["status_reason"] = "enabled", nil
			writeJSON(t, w, map[string]any{"plugin": item})
		case r.Method == http.MethodPost && strings.HasPrefix(r.URL.Path, base+"/acme.hello/"):
			action := strings.TrimPrefix(r.URL.Path, base+"/acme.hello/")
			item["status"], item["status_reason"] = map[string]string{
				"enable": "enabled", "disable": "disabled", "reload": "enabled",
			}[action], nil
			if action == "reload" {
				item["revision"] = 4
			}
			writeJSON(t, w, map[string]any{"plugin": item})
		case r.Method == http.MethodDelete && r.URL.Path == base+"/acme.hello":
			writeJSON(t, w, map[string]any{"removed": true, "automations_deleted": 2})
		case r.Method == http.MethodGet && r.URL.Path == base+"/acme.hello/logs":
			f.mu.Lock()
			f.logCalls++
			n := f.logCalls
			f.mu.Unlock()
			entries := []map[string]any{
				{"ts": "2026-10-07T10:00:00Z", "level": "info", "message": "loaded", "source": "frontend"},
				{"ts": "2026-10-07T10:00:01Z", "level": "warn", "message": "slow", "source": "frontend"},
				{"ts": "2026-10-07T10:00:02Z", "level": "info", "message": "denied /v1/projects", "source": "audit"},
			}
			// Each later poll appends one new entry (for -f).
			for i := 2; i <= n; i++ {
				entries = append(entries, map[string]any{
					"ts": "2026-10-07T10:01:0" + string(rune('0'+i)) + "Z", "level": "info",
					"message": "tick " + string(rune('0'+i)), "source": "frontend",
				})
			}
			writeJSON(t, w, map[string]any{"entries": entries})
		default:
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusNotFound)
			_ = json.NewEncoder(w).Encode(map[string]any{"error": map[string]any{"code": 404000, "message": "plugin not found"}})
		}
	})
	f.Server = httptest.NewServer(mux)
	t.Cleanup(f.Close)
	return f
}

func (f *fakePluginBackend) calls() []recordedRequest {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]recordedRequest(nil), f.requests...)
}

func (f *fakePluginBackend) find(method, path string) *recordedRequest {
	for _, r := range f.calls() {
		if r.Method == method && r.Path == path {
			r := r
			return &r
		}
	}
	return nil
}

// isolatePluginEnv points the CLI at fake servers with no ambient credentials.
func isolatePluginEnv(t *testing.T, backendURL string) {
	t.Helper()
	t.Setenv("HOME", t.TempDir())
	t.Setenv("VALUZ_BACKEND_BASE_URL", backendURL)
	t.Setenv("VALUZ_BACKEND_TOKEN", "")
	t.Setenv("VALUZ_API_KEY", "")
	t.Setenv("VALUZ_MANAGED", "")
	t.Setenv("VALUZ_CLOUD_URL", "")
}

// runPluginCmd runs `valuz <args>` with stdin and a context.
func runPluginCmd(t *testing.T, ctx context.Context, stdin string, args ...string) (string, string, error) {
	t.Helper()
	root := Root()
	var out, errBuf bytes.Buffer
	root.SetArgs(args)
	root.SetOut(&out)
	root.SetErr(&errBuf)
	root.SetIn(strings.NewReader(stdin))
	if ctx == nil {
		ctx = context.Background()
	}
	err := root.ExecuteContext(ctx)
	return out.String(), errBuf.String(), err
}

// writeSamplePlugin writes a valid plugin source directory.
func writeSamplePlugin(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	manifest := map[string]any{
		"manifestVersion": 1,
		"id":              "acme.hello",
		"version":         "1.2.0",
		"name":            map[string]any{"en-US": "Hello"},
		"publisher":       map[string]any{"name": "Acme"},
		"engines":         map[string]any{"valuz-plugin-api": "^1.0.0"},
		"frontend":        map[string]any{"entry": "frontend/index.js"},
		"permissions":     []string{"storage", "notifications"},
	}
	raw, _ := json.Marshal(manifest)
	for name, content := range map[string]string{
		pluginpkg.ManifestFile: string(raw),
		"frontend/index.js":    "export default definePlugin({ id: 'acme.hello' })",
		"locales/en-US.json":   `{"title":"Hello"}`,
	} {
		p := filepath.Join(dir, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

// ── validate / pack ─────────────────────────────────────────────────────────

func TestPluginValidateOK(t *testing.T) {
	isolatePluginEnv(t, "http://127.0.0.1:1")
	dir := writeSamplePlugin(t)
	out, _, err := runPluginCmd(t, nil, "", "plugin", "app", "validate", dir)
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}
	if !strings.Contains(out, "acme.hello 1.2.0: ok") {
		t.Fatalf("unexpected:\n%s", out)
	}
}

func TestPluginValidateJSONAndFailureExit(t *testing.T) {
	isolatePluginEnv(t, "http://127.0.0.1:1")
	dir := writeSamplePlugin(t)
	raw := `{"manifestVersion":1,"id":"valuz.x","version":"1","name":"x","publisher":{"name":"a"},` +
		`"engines":{"valuz-plugin-api":"^1.0.0"},"frontend":{"entry":"frontend/index.js"},"backend":{}}`
	if err := os.WriteFile(filepath.Join(dir, pluginpkg.ManifestFile), []byte(raw), 0o644); err != nil {
		t.Fatal(err)
	}
	out, _, err := runPluginCmd(t, nil, "", "plugin", "app", "validate", dir, "-o", "json")
	if err == nil || !strings.Contains(err.Error(), "plugin is invalid") {
		t.Fatalf("want a non-zero exit, got %v", err)
	}
	var res pluginpkg.Result
	if jerr := json.Unmarshal([]byte(out), &res); jerr != nil {
		t.Fatalf("not JSON: %v\n%s", jerr, out)
	}
	joined := strings.Join(res.Errors, "\n")
	for _, want := range []string{"reserved prefix", "version:", pluginpkg.ErrBackendUnsupported} {
		if !strings.Contains(joined, want) {
			t.Fatalf("missing %q in %q", want, res.Errors)
		}
	}
	if res.OK || res.Manifest["id"] != "valuz.x" {
		t.Fatalf("unexpected: %+v", res)
	}
}

func TestPluginPackPrintsPathAndSHA(t *testing.T) {
	isolatePluginEnv(t, "http://127.0.0.1:1")
	dir := writeSamplePlugin(t)
	outDir := t.TempDir()
	out, _, err := runPluginCmd(t, nil, "", "plugin", "app", "pack", dir, "--out", outDir, "-o", "json")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	var res pluginpkg.PackResult
	if err := json.Unmarshal([]byte(out), &res); err != nil {
		t.Fatalf("not JSON: %v\n%s", err, out)
	}
	data, err := os.ReadFile(filepath.Join(outDir, "acme.hello-1.2.0.zip"))
	if err != nil || pluginpkg.SHA256Hex(data) != res.SHA256 {
		t.Fatalf("zip/sha mismatch: %v", err)
	}
	human, _, err := runPluginCmd(t, nil, "", "plugin", "app", "pack", dir, "--out", outDir)
	if err != nil || !strings.Contains(human, "sha256  "+res.SHA256) || !strings.Contains(human, res.Path) {
		t.Fatalf("human output: %v\n%s", err, human)
	}
}

// ── local backend ───────────────────────────────────────────────────────────

func TestPluginInstallShowsSummaryAndStopsOnNo(t *testing.T) {
	f := newFakePluginBackend(t)
	isolatePluginEnv(t, f.URL)
	zipPath := filepath.Join(t.TempDir(), "acme.hello-1.2.0.zip")
	if err := os.WriteFile(zipPath, []byte("zip"), 0o644); err != nil {
		t.Fatal(err)
	}
	out, _, err := runPluginCmd(t, nil, "n\n", "plugin", "app", "install", zipPath)
	if err == nil || !strings.Contains(err.Error(), "cancelled") {
		t.Fatalf("want cancellation, got %v", err)
	}
	for _, want := range []string{
		"plugin:      acme.hello",
		"version:     1.2.0 (updates 1.1.0)",
		"publisher:   Acme",
		"source:      " + zipPath,
		"sha256:      ab12cd34",
		"permissions: storage, notifications (new)",
		"requires:    edition:finance (unmet)",
		"warning: icon:",
		installQuestion,
	} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q in:\n%s", want, out)
		}
	}
	insp := f.find(http.MethodPost, "/v1/extensions/third-party/inspect")
	if insp == nil || insp.Body["source_path"] != zipPath {
		t.Fatalf("inspect request: %+v", insp)
	}
	if f.find(http.MethodPost, "/v1/extensions/third-party/install") != nil {
		t.Fatal("installed although the user said no")
	}
}

func TestPluginInstallConfirmedSendsExpectedSHA(t *testing.T) {
	f := newFakePluginBackend(t)
	isolatePluginEnv(t, f.URL)
	dir := writeSamplePlugin(t)
	out, _, err := runPluginCmd(t, nil, "y\n", "plugin", "app", "install", dir)
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}
	inst := f.find(http.MethodPost, "/v1/extensions/third-party/install")
	if inst == nil || inst.Body["source_path"] != dir || inst.Body["expected_sha256"] != "ab12cd34" {
		t.Fatalf("install request: %+v", inst)
	}
	if !strings.Contains(out, "updated acme.hello 1.2.0 (from 1.1.0)") || !strings.Contains(out, "status: enabled") {
		t.Fatalf("unexpected:\n%s", out)
	}
}

func TestPluginInstallURLWithYesAndJSON(t *testing.T) {
	f := newFakePluginBackend(t)
	isolatePluginEnv(t, f.URL)
	const u = "https://plugins.example/acme.hello-1.2.0.zip"
	out, stderr, err := runPluginCmd(t, nil, "", "plugin", "app", "install", u, "--yes", "-o", "json")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	insp := f.find(http.MethodPost, "/v1/extensions/third-party/inspect")
	inst := f.find(http.MethodPost, "/v1/extensions/third-party/install")
	if insp == nil || insp.Body["url"] != u || insp.Body["source_path"] != nil || inst == nil || inst.Body["url"] != u {
		t.Fatalf("requests: %+v %+v", insp, inst)
	}
	var doc map[string]json.RawMessage
	if err := json.Unmarshal([]byte(out), &doc); err != nil || doc["inspect"] == nil || doc["install"] == nil {
		t.Fatalf("stdout is not the {inspect, install} document: %v\n%s", err, out)
	}
	if stderr != "" {
		t.Fatalf("--yes -o json printed a summary: %q", stderr)
	}
}

func TestPluginInstallAbortsOnInspectErrors(t *testing.T) {
	f := newFakePluginBackend(t)
	f.inspectErrors = []any{"backend: " + pluginpkg.ErrBackendUnsupported, map[string]any{"path": "id", "message": "reserved"}}
	isolatePluginEnv(t, f.URL)
	dir := writeSamplePlugin(t)
	out, _, err := runPluginCmd(t, nil, "", "plugin", "app", "install", dir, "--yes")
	if err == nil || !strings.Contains(err.Error(), "cannot be installed: 2 error(s)") {
		t.Fatalf("want an abort, got %v", err)
	}
	if !strings.Contains(out, "error: backend: B-level") || !strings.Contains(out, "error: id: reserved") {
		t.Fatalf("errors not shown:\n%s", out)
	}
	if f.find(http.MethodPost, "/v1/extensions/third-party/install") != nil {
		t.Fatal("installed despite inspect errors")
	}
}

func TestPluginInstallSurfacesServerErrors(t *testing.T) {
	f := newFakePluginBackend(t)
	f.installStatus = http.StatusBadRequest
	f.installBody = map[string]any{"error": map[string]any{
		"code": "invalid_manifest", "message": "invalid manifest",
		"errors": []any{"id: is required", map[string]any{"path": "version", "message": "not SemVer"}},
	}}
	isolatePluginEnv(t, f.URL)
	_, _, err := runPluginCmd(t, nil, "", "plugin", "app", "install", writeSamplePlugin(t), "--yes")
	if err == nil || !strings.Contains(err.Error(), "invalid manifest: id: is required; version: not SemVer") {
		t.Fatalf("want the server findings, got %v", err)
	}
}

func TestPluginInstallMissingPath(t *testing.T) {
	f := newFakePluginBackend(t)
	isolatePluginEnv(t, f.URL)
	_, _, err := runPluginCmd(t, nil, "", "plugin", "app", "install", filepath.Join(t.TempDir(), "nope.zip"))
	if err == nil || len(f.calls()) != 0 {
		t.Fatalf("want a local error before any request: %v %v", err, f.calls())
	}
}

func TestPluginDevValidatesThenLinks(t *testing.T) {
	f := newFakePluginBackend(t)
	isolatePluginEnv(t, f.URL)
	dir := writeSamplePlugin(t)
	out, _, err := runPluginCmd(t, nil, "", "plugin", "app", "dev", dir)
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	link := f.find(http.MethodPost, "/v1/extensions/third-party/dev-link")
	if link == nil || link.Body["path"] != dir {
		t.Fatalf("dev-link request: %+v", link)
	}
	for _, want := range []string{"linked   acme.hello 1.2.0 (dev)", "revision 3", "entry    /v1/ext-assets/acme.hello/3/frontend/index.js"} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q in:\n%s", want, out)
		}
	}
}

func TestPluginDevRefusesInvalidDir(t *testing.T) {
	f := newFakePluginBackend(t)
	isolatePluginEnv(t, f.URL)
	dir := writeSamplePlugin(t)
	if err := os.Remove(filepath.Join(dir, "frontend", "index.js")); err != nil {
		t.Fatal(err)
	}
	out, _, err := runPluginCmd(t, nil, "", "plugin", "app", "dev", dir)
	if err == nil || !strings.Contains(out, "frontend.entry") || len(f.calls()) != 0 {
		t.Fatalf("want a local validation failure: %v\n%s", err, out)
	}
}

func TestPluginListAndStatus(t *testing.T) {
	f := newFakePluginBackend(t)
	isolatePluginEnv(t, f.URL)
	out, _, err := runPluginCmd(t, nil, "", "plugin", "app", "list")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if !strings.Contains(out, "Safe mode is on (renderer crashed twice)") {
		t.Fatalf("safe mode banner missing:\n%s", out)
	}
	var row string
	for _, line := range strings.Split(out, "\n") {
		if strings.HasPrefix(line, "acme.hello") {
			row = line
		}
	}
	if strings.Join(strings.Fields(row), " ") != "acme.hello 1.2.0 requires-unmet dev 3" {
		t.Fatalf("row = %q\n%s", row, out)
	}

	out, _, err = runPluginCmd(t, nil, "", "plugin", "app", "status", "acme.hello")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	for _, want := range []string{
		"status:         requires-unmet", "status reason:  needs edition:finance",
		"unmet requires: edition:finance", "permissions:    storage, notifications",
		"source:         dev /src/acme-hello", "automations:    daily",
	} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q in:\n%s", want, out)
		}
	}

	out, _, err = runPluginCmd(t, nil, "", "plugin", "app", "status", "acme.hello", "-o", "json")
	var item map[string]any
	if err != nil || json.Unmarshal([]byte(out), &item) != nil || item["id"] != "acme.hello" {
		t.Fatalf("json status: %v\n%s", err, out)
	}

	_, _, err = runPluginCmd(t, nil, "", "plugin", "app", "status", "acme.other")
	if err == nil || !strings.Contains(err.Error(), `"acme.other" is not installed`) {
		t.Fatalf("want not-installed, got %v", err)
	}
}

func TestPluginLogs(t *testing.T) {
	f := newFakePluginBackend(t)
	isolatePluginEnv(t, f.URL)
	out, _, err := runPluginCmd(t, nil, "", "plugin", "app", "logs", "acme.hello", "-n", "2")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	req := f.find(http.MethodGet, "/v1/extensions/third-party/acme.hello/logs")
	if req == nil || req.Query != "limit=2" {
		t.Fatalf("logs request: %+v", req)
	}
	// The fake returns 3 entries; -n 2 keeps the newest two.
	lines := strings.Split(strings.TrimSpace(out), "\n")
	if len(lines) != 2 || !strings.Contains(lines[0], "WARN  [frontend] slow") ||
		!strings.Contains(lines[1], "INFO  [audit] denied /v1/projects") {
		t.Fatalf("unexpected:\n%s", out)
	}
}

func TestPluginLogsFollowPrintsOnlyNewEntries(t *testing.T) {
	f := newFakePluginBackend(t)
	isolatePluginEnv(t, f.URL)
	old := pluginLogsPollInterval
	pluginLogsPollInterval = 20 * time.Millisecond
	t.Cleanup(func() { pluginLogsPollInterval = old })
	ctx, cancel := context.WithTimeout(context.Background(), 400*time.Millisecond)
	defer cancel()
	out, _, err := runPluginCmd(t, ctx, "", "plugin", "app", "logs", "acme.hello", "-f")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	for _, msg := range []string{"loaded", "slow", "tick 2", "tick 3"} {
		if c := strings.Count(out, msg+"\n"); c != 1 {
			t.Fatalf("%q printed %d times:\n%s", msg, c, out)
		}
	}
}

func TestPluginEnableDisableReload(t *testing.T) {
	f := newFakePluginBackend(t)
	isolatePluginEnv(t, f.URL)
	for verb, want := range map[string]string{
		"enable":  "acme.hello: enabled (status enabled)",
		"disable": "acme.hello: disabled (status disabled)",
		"reload":  "acme.hello: reloaded (revision 4, status enabled)",
	} {
		out, _, err := runPluginCmd(t, nil, "", "plugin", "app", verb, "acme.hello")
		if err != nil || !strings.Contains(out, want) {
			t.Fatalf("%s: %v\n%s", verb, err, out)
		}
		if f.find(http.MethodPost, "/v1/extensions/third-party/acme.hello/"+verb) == nil {
			t.Fatalf("%s: no request", verb)
		}
	}
	_, _, err := runPluginCmd(t, nil, "", "plugin", "app", "enable", "acme.missing")
	if err == nil || !strings.Contains(err.Error(), "plugin not found") {
		t.Fatalf("want the 404 message, got %v", err)
	}
}

func TestPluginUninstall(t *testing.T) {
	f := newFakePluginBackend(t)
	isolatePluginEnv(t, f.URL)
	out, _, err := runPluginCmd(t, nil, "", "plugin", "app", "uninstall", "acme.hello")
	if err == nil || !strings.Contains(out, "Uninstall acme.hello? Its data is kept. [y/N]") {
		t.Fatalf("want a refused prompt: %v\n%s", err, out)
	}
	if f.find(http.MethodDelete, "/v1/extensions/third-party/acme.hello") != nil {
		t.Fatal("uninstalled without confirmation")
	}
	out, _, err = runPluginCmd(t, nil, "", "plugin", "app", "uninstall", "acme.hello", "--purge-data", "--yes")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	del := f.find(http.MethodDelete, "/v1/extensions/third-party/acme.hello")
	if del == nil || del.Query != "purge_data=true" {
		t.Fatalf("delete request: %+v", del)
	}
	if !strings.Contains(out, "uninstalled acme.hello (2 automations deleted, data deleted)") {
		t.Fatalf("unexpected:\n%s", out)
	}
}

// ── control plane ───────────────────────────────────────────────────────────

// fakeControlPlane serves <cloud>/v1/extensions/{submissions,catalog} under
// the /cloud prefix and records the multipart submissions.
type fakeControlPlane struct {
	*httptest.Server
	mu          sync.Mutex
	submissions []submissionRecord
	auths       []string
	status      string
}

type submissionRecord struct {
	Fields   map[string][]string
	FileName string
	FileSHA  string
	FileType string
}

func newFakeControlPlane(t *testing.T) *fakeControlPlane {
	t.Helper()
	f := &fakeControlPlane{status: "pending"}
	mux := http.NewServeMux()
	mux.HandleFunc("/cloud/v1/extensions/submissions", func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		f.auths = append(f.auths, r.Header.Get("Authorization"))
		f.mu.Unlock()
		if r.Header.Get("Authorization") == "" {
			w.WriteHeader(http.StatusUnauthorized)
			_ = json.NewEncoder(w).Encode(map[string]any{"detail": map[string]any{"error": map[string]any{
				"code": "auth.missing_bearer", "message": "Bearer token required"}}})
			return
		}
		if r.Method == http.MethodGet {
			writeJSON(t, w, map[string]any{"items": []map[string]any{
				{"id": "sub_1", "extension_id": "acme.hello", "version": "1.2.0", "scope": "org",
					"status": "pending", "created_at": "2026-10-07T10:00:00Z"},
			}, "total": 1})
			return
		}
		if err := r.ParseMultipartForm(8 << 20); err != nil {
			t.Errorf("parse multipart: %v", err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		file, hdr, err := r.FormFile("file")
		if err != nil {
			t.Errorf("file part: %v", err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		data, _ := io.ReadAll(file)
		f.mu.Lock()
		f.submissions = append(f.submissions, submissionRecord{
			Fields: r.MultipartForm.Value, FileName: hdr.Filename,
			FileSHA: pluginpkg.SHA256Hex(data), FileType: hdr.Header.Get("Content-Type"),
		})
		status := f.status
		f.mu.Unlock()
		writeJSON(t, w, map[string]any{
			"id": "sub_1", "status": status, "reason": map[bool]string{true: "manifest uses a reserved id"}[status == "rejected"],
			"checks": []map[string]any{
				{"name": "manifest", "status": "pass"},
				{"name": "size", "ok": true, "message": "2 KB"},
			},
		})
	})
	mux.HandleFunc("/cloud/v1/extensions/catalog", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, map[string]any{"items": []map[string]any{{
			"extension_id": "acme.hello",
			"versions": []map[string]any{
				{"version": "1.0.0", "permissions": []string{}},
				{"version": "1.1.0", "permissions": []string{"storage"}},
			},
		}}})
	})
	f.Server = httptest.NewServer(mux)
	t.Cleanup(f.Close)
	return f
}

func (f *fakeControlPlane) recorded() []submissionRecord {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]submissionRecord(nil), f.submissions...)
}

// packSample packs writeSamplePlugin and returns the zip path + its sha256.
func packSample(t *testing.T) (string, string) {
	t.Helper()
	res, err := pluginpkg.Pack(writeSamplePlugin(t), t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	return res.Path, res.SHA256
}

func TestPluginPublishWithAPIKey(t *testing.T) {
	cp := newFakeControlPlane(t)
	isolatePluginEnv(t, "http://127.0.0.1:1")
	zipPath, sha := packSample(t)
	const key = "vzp_test_key_123456"
	out, stderr, err := runPluginCmd(t, nil, "", "plugin", "app", "publish", zipPath,
		"--scope", "org", "--notes", "first release", "--api-key", key, "--yes", "--cloud-url", cp.URL+"/cloud")
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}
	subs := cp.recorded()
	if len(subs) != 1 {
		t.Fatalf("submissions = %d", len(subs))
	}
	s := subs[0]
	if s.FileSHA != sha || s.FileName != filepath.Base(zipPath) || s.FileType != "application/zip" {
		t.Fatalf("file part: %+v", s)
	}
	if s.Fields["scope"][0] != "org" || s.Fields["notes"][0] != "first release" || len(s.Fields["distribution_ids"]) != 0 {
		t.Fatalf("fields: %+v", s.Fields)
	}
	for _, a := range cp.auths {
		if a != "Bearer "+key {
			t.Fatalf("auth header = %q", a)
		}
	}
	for _, want := range []string{
		"plugin:      acme.hello", "version:     1.2.0 (updates 1.1.0)", "sha256:      " + sha,
		"permissions: storage, notifications (new)", "scope:       org", "target:      your current org",
		"submission sub_1", "status     pending", "pass   manifest", "pass   size: 2 KB",
	} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q in:\n%s", want, out)
		}
	}
	if strings.Contains(out+stderr, key) {
		t.Fatal("the API key was printed")
	}
}

func TestPluginPublishGlobalSendsRepeatedDistributions(t *testing.T) {
	cp := newFakeControlPlane(t)
	isolatePluginEnv(t, "http://127.0.0.1:1")
	t.Setenv("VALUZ_CLOUD_URL", cp.URL+"/cloud")
	t.Setenv("VALUZ_API_KEY", "vzp_env_key_123456")
	zipPath, _ := packSample(t)
	out, _, err := runPluginCmd(t, nil, "", "plugin", "app", "publish", zipPath,
		"--scope", "global", "--distribution", "dist_a", "--distribution", "dist_b", "--yes", "-o", "json")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	subs := cp.recorded()
	if len(subs) != 1 || strings.Join(subs[0].Fields["distribution_ids"], ",") != "dist_a,dist_b" ||
		subs[0].Fields["scope"][0] != "global" {
		t.Fatalf("submissions: %+v", subs)
	}
	var doc map[string]any
	if err := json.Unmarshal([]byte(out), &doc); err != nil || doc["id"] != "sub_1" {
		t.Fatalf("json: %v\n%s", err, out)
	}
}

func TestPluginPublishConfirmation(t *testing.T) {
	cp := newFakeControlPlane(t)
	isolatePluginEnv(t, "http://127.0.0.1:1")
	t.Setenv("VALUZ_CLOUD_URL", cp.URL+"/cloud")
	t.Setenv("VALUZ_API_KEY", "vzp_env_key_123456")
	zipPath, _ := packSample(t)
	out, _, err := runPluginCmd(t, nil, "no\n", "plugin", "app", "publish", zipPath, "--scope", "personal")
	if err == nil || !strings.Contains(err.Error(), "publish cancelled") ||
		!strings.Contains(out, "Publish acme.hello 1.2.0 to your personal catalog? [y/N]") {
		t.Fatalf("want a refused prompt: %v\n%s", err, out)
	}
	if len(cp.recorded()) != 0 {
		t.Fatal("uploaded without confirmation")
	}
	if _, _, err := runPluginCmd(t, nil, "y\n", "plugin", "app", "publish", zipPath, "--scope", "personal"); err != nil {
		t.Fatalf("confirmed publish: %v", err)
	}
	if len(cp.recorded()) != 1 {
		t.Fatal("confirmed publish did not upload")
	}
}

func TestPluginPublishFlagValidation(t *testing.T) {
	cp := newFakeControlPlane(t)
	isolatePluginEnv(t, "http://127.0.0.1:1")
	t.Setenv("VALUZ_CLOUD_URL", cp.URL+"/cloud")
	zipPath, _ := packSample(t)
	for _, tc := range []struct {
		args []string
		want string
	}{
		{[]string{"--yes"}, "--scope is required"},
		{[]string{"--scope", "team", "--yes"}, "unsupported --scope"},
		{[]string{"--scope", "global", "--yes"}, "at least one --distribution"},
		{[]string{"--scope", "org", "--distribution", "d", "--yes"}, "only applies to --scope global"},
		{[]string{"--scope", "org", "--api-key", "sk-not-personal", "--yes"}, "must be a personal API key"},
		{[]string{"--scope", "org", "--yes"}, "not logged in to Valuz"},
	} {
		args := append([]string{"plugin", "app", "publish", zipPath}, tc.args...)
		_, _, err := runPluginCmd(t, nil, "", args...)
		if err == nil || !strings.Contains(err.Error(), tc.want) {
			t.Fatalf("%v: want %q, got %v", tc.args, tc.want, err)
		}
	}
	if len(cp.recorded()) != 0 {
		t.Fatal("an invalid invocation uploaded")
	}
}

func TestPluginPublishRefusesInvalidZip(t *testing.T) {
	cp := newFakeControlPlane(t)
	isolatePluginEnv(t, "http://127.0.0.1:1")
	t.Setenv("VALUZ_CLOUD_URL", cp.URL+"/cloud")
	t.Setenv("VALUZ_API_KEY", "vzp_env_key_123456")
	bad := filepath.Join(t.TempDir(), "bad.zip")
	if err := os.WriteFile(bad, []byte("not a zip"), 0o644); err != nil {
		t.Fatal(err)
	}
	_, _, err := runPluginCmd(t, nil, "", "plugin", "app", "publish", bad, "--scope", "org", "--yes")
	if err == nil || len(cp.recorded()) != 0 {
		t.Fatalf("want a local failure without upload, got %v", err)
	}
}

func TestPluginPublishRejectedExitsNonZero(t *testing.T) {
	cp := newFakeControlPlane(t)
	cp.status = "rejected"
	isolatePluginEnv(t, "http://127.0.0.1:1")
	t.Setenv("VALUZ_CLOUD_URL", cp.URL+"/cloud")
	t.Setenv("VALUZ_API_KEY", "vzp_env_key_123456")
	zipPath, _ := packSample(t)
	out, _, err := runPluginCmd(t, nil, "", "plugin", "app", "publish", zipPath, "--scope", "personal", "--yes")
	if err == nil || !strings.Contains(err.Error(), "rejected") || !strings.Contains(out, "reason     manifest uses a reserved id") {
		t.Fatalf("want a rejected exit: %v\n%s", err, out)
	}
}

func TestPluginPublishUsesStoredLogin(t *testing.T) {
	cp := newFakeControlPlane(t)
	isolatePluginEnv(t, "http://127.0.0.1:1")
	t.Setenv("VALUZ_CLOUD_URL", cp.URL+"/cloud")
	home, _ := os.UserHomeDir()
	authDir := filepath.Join(home, ".valuz-oss")
	if err := os.MkdirAll(authDir, 0o700); err != nil {
		t.Fatal(err)
	}
	state := map[string]any{
		"access_token": "jwt-access-token", "refresh_token": "r", "expires_in": 3600,
		"expires_at": time.Now().Add(time.Hour).Format(time.RFC3339),
		"principal":  map[string]any{"master_id": "m", "distribution": "d", "org_name": "Acme Research"},
	}
	raw, _ := json.Marshal(state)
	if err := os.WriteFile(filepath.Join(authDir, "auth.json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}
	zipPath, _ := packSample(t)
	out, _, err := runPluginCmd(t, nil, "", "plugin", "app", "publish", zipPath, "--scope", "org", "--yes")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if !strings.Contains(out, `target:      org "Acme Research"`) {
		t.Fatalf("org target missing:\n%s", out)
	}
	cp.mu.Lock()
	defer cp.mu.Unlock()
	for _, a := range cp.auths {
		if a != "Bearer jwt-access-token" {
			t.Fatalf("auth header = %q", a)
		}
	}
}

func TestPluginSubmissions(t *testing.T) {
	cp := newFakeControlPlane(t)
	isolatePluginEnv(t, "http://127.0.0.1:1")
	t.Setenv("VALUZ_CLOUD_URL", cp.URL+"/cloud")
	t.Setenv("VALUZ_API_KEY", "vzp_env_key_123456")
	out, _, err := runPluginCmd(t, nil, "", "plugin", "app", "submissions")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	var row string
	for _, line := range strings.Split(out, "\n") {
		if strings.HasPrefix(line, "sub_1") {
			row = line
		}
	}
	if strings.Join(strings.Fields(row), " ") != "sub_1 acme.hello 1.2.0 org pending 2026-10-07T10:00:00Z" {
		t.Fatalf("row = %q\n%s", row, out)
	}
	out, _, err = runPluginCmd(t, nil, "", "plugin", "app", "submissions", "-o", "json")
	var doc map[string]any
	if err != nil || json.Unmarshal([]byte(out), &doc) != nil || doc["total"] == nil {
		t.Fatalf("json: %v\n%s", err, out)
	}
}

func TestControlPlaneNestedDetailErrorIsReadable(t *testing.T) {
	cp := newFakeControlPlane(t)
	// No bearer: the fake answers 401 {"detail": {"error": {...}}} like the
	// control plane's HTTPException.
	c := newControlClient(&RootOptions{BackendURL: cp.URL + "/cloud"}, "")
	var raw json.RawMessage
	err := c.Get(context.Background(), submissionsAPI, &raw)
	if err == nil || !strings.Contains(err.Error(), "HTTP 401: Bearer token required") {
		t.Fatalf("want the nested detail.error message, got %v", err)
	}
}

package cmd

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
)

const agentPluginFixture = `{"id":"agent-1","name":"research","version":"1.0.0","description":"Research skills","source":"local_dir","source_ref":"/srv/research","composition":"with_connectors","enabled":true,"deletable":true,"protected":false,"skill_count":1,"connector_count":1,"members":[{"kind":"skill","slug":"analysis","name":"Analysis","content_hash":"abc","installed":true,"content_differs":false},{"kind":"connector","slug":"quotes","name":"Quotes","content_hash":"def","installed":true,"content_differs":true}],"root_path":"/plugins/research","installed_at":"2026-10-07T00:00:00Z","updated_at":"2026-10-07T00:00:00Z"}`

type agentPluginRequest struct {
	Method        string
	Path          string
	Body          map[string]string
	FileName      string
	FileData      string
	Authorization string
}

type fakeAgentPluginBackend struct {
	*httptest.Server
	mu           sync.Mutex
	requests     []agentPluginRequest
	Existing     string
	ExportStatus int
	ExportBody   string
	DuringExport func()
}

func useAgentPluginBackend(t *testing.T) *fakeAgentPluginBackend {
	t.Helper()
	f := &fakeAgentPluginBackend{ExportStatus: http.StatusOK, ExportBody: "PK exported agent plugin"}
	f.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		req := agentPluginRequest{Method: r.Method, Path: r.URL.EscapedPath(), Authorization: r.Header.Get("Authorization")}
		if r.Method == http.MethodPost {
			if strings.HasPrefix(r.Header.Get("Content-Type"), "multipart/form-data") {
				if err := r.ParseMultipartForm(1 << 20); err != nil {
					t.Errorf("multipart: %v", err)
				}
				if r.MultipartForm != nil {
					defer r.MultipartForm.RemoveAll()
				}
				req.Body = map[string]string{"on_conflict": r.FormValue("on_conflict")}
				file, header, err := r.FormFile("file")
				if err != nil {
					t.Errorf("file field: %v", err)
				} else {
					defer file.Close()
					data, _ := io.ReadAll(file)
					req.FileName, req.FileData = header.Filename, string(data)
				}
			} else {
				body, _ := io.ReadAll(r.Body)
				if len(body) > 0 {
					if r.Header.Get("Content-Type") != "application/json" {
						t.Errorf("content type = %s", r.Header.Get("Content-Type"))
					}
					if err := json.Unmarshal(body, &req.Body); err != nil {
						t.Errorf("JSON request: %v", err)
					}
				}
			}
		}
		f.mu.Lock()
		f.requests = append(f.requests, req)
		f.mu.Unlock()
		if req.Authorization != "Bearer agent-test-token" {
			t.Errorf("authorization = %q", req.Authorization)
		}
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.URL.Path == agentPluginAPI && r.Method == http.MethodGet:
			fmt.Fprintf(w, `{"items":[%s]}`, agentPluginFixture)
		case r.URL.Path == agentPluginAPI+"/preview" && r.Method == http.MethodPost:
			var existing any
			if f.Existing != "" {
				existing = f.Existing
			}
			writeJSON(t, w, map[string]any{"manifest": map[string]any{"name": "research", "version": "1.0.0"}, "format": "agent_plugins", "composition": "with_connectors", "members": []map[string]any{{"kind": "skill", "slug": "analysis", "name": "Analysis", "content_hash": "abc", "installed": true, "content_differs": true}}, "conflicts": []map[string]string{{"kind": "skill", "slug": "analysis"}}, "skipped": []map[string]string{{"kind": "mcp", "slug": "unsupported", "reason": "unsupported_transport"}}, "warnings": []string{"review connector access"}, "existing": existing})
		case r.URL.Path == agentPluginAPI+"/install" && r.Method == http.MethodPost,
			r.URL.Path == agentPluginAPI+"/agent-1/update" && r.Method == http.MethodPost:
			fmt.Fprintf(w, `{"plugin":%s,"status":"installed","conflicts":[{"kind":"skill","slug":"analysis"}],"skipped":[{"kind":"skill","slug":"analysis","reason":"conflict"}],"warnings":["review connector access"]}`, agentPluginFixture)
		case r.URL.Path == agentPluginAPI+"/agent-1" && r.Method == http.MethodDelete:
			fmt.Fprint(w, `{"removed_members":[{"kind":"skill","slug":"analysis"}],"kept_members":[{"kind":"connector","slug":"quotes","reason":"referenced_by_other_plugin"}]}`)
		case strings.HasSuffix(r.URL.Path, "/export"):
			if f.DuringExport != nil {
				f.DuringExport()
			}
			if f.ExportStatus != http.StatusOK {
				w.WriteHeader(f.ExportStatus)
				fmt.Fprint(w, `{"detail":"export forbidden or missing"}`)
			} else {
				w.Header().Set("Content-Type", "application/zip")
				fmt.Fprint(w, f.ExportBody)
			}
		case r.URL.Path == agentPluginAPI+"/agent-1" && r.Method == http.MethodGet,
			r.URL.Path == agentPluginAPI+"/agent-1/enable" && r.Method == http.MethodPost,
			r.URL.Path == agentPluginAPI+"/agent-1/disable" && r.Method == http.MethodPost:
			fmt.Fprint(w, agentPluginFixture)
		default:
			t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(f.Close)
	t.Setenv("HOME", t.TempDir())
	t.Setenv("VALUZ_BACKEND_BASE_URL", f.URL)
	t.Setenv("VALUZ_BACKEND_TOKEN", "agent-test-token")
	return f
}

func (f *fakeAgentPluginBackend) calls() []agentPluginRequest {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]agentPluginRequest(nil), f.requests...)
}

func TestAgentPluginListAndShow(t *testing.T) {
	for _, verb := range []string{"list", "show"} {
		t.Run(verb, func(t *testing.T) {
			f := useAgentPluginBackend(t)
			args := []string{"plugin", "agent", verb}
			if verb == "show" {
				args = append(args, "agent-1")
			}
			out, _, err := runCmd(t, Root(), args...)
			if err != nil {
				t.Fatal(err)
			}
			for _, want := range []string{"agent-1", "research", "1.0.0", "local_dir"} {
				if !strings.Contains(out, want) {
					t.Fatalf("missing %q in %s", want, out)
				}
			}
			if verb == "show" && !strings.Contains(out, "library content differs") {
				t.Fatalf("member content status missing: %s", out)
			}
			args = append(args, "-o", "json")
			out, _, err = runCmd(t, Root(), args...)
			if err != nil || !json.Valid([]byte(out)) {
				t.Fatalf("JSON: %v %s", err, out)
			}
			if calls := f.calls(); len(calls) != 2 || calls[0].Method != http.MethodGet {
				t.Fatalf("calls: %+v", calls)
			}
		})
	}
}

func TestAgentPluginPreviewSourcesUseActualAPIFields(t *testing.T) {
	for _, tc := range []struct {
		name         string
		args         []string
		field, value string
	}{
		{"directory", []string{"/srv/research"}, "path", "/srv/research"},
		{"url", []string{"https://example.com/research.zip"}, "url", "https://example.com/research.zip"},
		{"market", []string{"--market-item", "market-1"}, "market_item_id", "market-1"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := useAgentPluginBackend(t)
			args := append([]string{"plugin", "agent", "preview"}, tc.args...)
			args = append(args, "-o", "json")
			out, stderr, err := runCmd(t, Root(), args...)
			if err != nil || !json.Valid([]byte(out)) || stderr != "" {
				t.Fatalf("preview: %v stdout=%s stderr=%s", err, out, stderr)
			}
			calls := f.calls()
			if len(calls) != 1 || calls[0].Path != agentPluginAPI+"/preview" || calls[0].Method != http.MethodPost || calls[0].Body[tc.field] != tc.value || calls[0].Body["on_conflict"] != "skip" || len(calls[0].Body) != 2 {
				t.Fatalf("calls: %+v", calls)
			}
		})
	}
}

func TestAgentPluginZIPPreviewAndInstallUploadExactInspectedBytes(t *testing.T) {
	f := useAgentPluginBackend(t)
	zipPath := filepath.Join(t.TempDir(), "research.zip")
	if err := os.WriteFile(zipPath, []byte("PK test package"), 0600); err != nil {
		t.Fatal(err)
	}
	out, stderr, err := runCmd(t, Root(), "plugin", "agent", "install", zipPath, "--on-conflict", "overwrite", "--yes", "-o", "json")
	if err != nil || !json.Valid([]byte(out)) {
		t.Fatalf("install: %v %s", err, out)
	}
	if strings.Contains(out, "Agent Plugin:") || !strings.Contains(stderr, "conflict policy: overwrite") || !strings.Contains(stderr, "review connector access") {
		t.Fatalf("stdout=%s stderr=%s", out, stderr)
	}
	calls := f.calls()
	if len(calls) != 2 || calls[0].Path != agentPluginAPI+"/preview" || calls[1].Path != agentPluginAPI+"/install" {
		t.Fatalf("calls: %+v", calls)
	}
	for _, c := range calls {
		if c.FileName != "research.zip" || c.FileData != "PK test package" || c.Body["on_conflict"] != "overwrite" || len(c.Body) != 1 {
			t.Fatalf("upload: %+v", c)
		}
	}
	var result agentPluginInstallResult
	if err := json.Unmarshal([]byte(out), &result); err != nil || result.Plugin.ID != "agent-1" || result.Status != "installed" {
		t.Fatalf("result: %+v %v", result, err)
	}
}

func TestAgentPluginInstallRequiresConfirmationAndRejectsOtherSource(t *testing.T) {
	for _, tc := range []struct {
		name, input, existing string
		yes                   bool
		wantInstall           bool
	}{
		{"EOF", "", "", false, false}, {"no", "n\n", "", false, false},
		{"yes", "yes\n", "", false, true}, {"same-source", "", "same_source", true, true},
		{"other-source", "", "other_source", true, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := useAgentPluginBackend(t)
			f.Existing = tc.existing
			root := Root()
			root.SetIn(strings.NewReader(tc.input))
			args := []string{"plugin", "agent", "install", "https://example.com/plugin.zip", "-o", "json"}
			if tc.yes {
				args = append(args, "--yes")
			}
			out, stderr, err := runCmd(t, root, args...)
			calls := f.calls()
			if tc.wantInstall {
				if err != nil || len(calls) != 2 || !json.Valid([]byte(out)) {
					t.Fatalf("install: %v %+v %s", err, calls, out)
				}
				if calls[1].Body["on_conflict"] != "skip" {
					t.Fatalf("default conflict policy: %+v", calls[1])
				}
			} else if err == nil || len(calls) != 1 || out != "" {
				t.Fatalf("must not install: %v %+v stdout=%s", err, calls, out)
			}
			if !strings.Contains(stderr, "conflict: skill analysis") || !strings.Contains(stderr, "warning:") {
				t.Fatalf("summary missing: %s", stderr)
			}
		})
	}
}

func TestAgentPluginSourceValidationMakesNoRequests(t *testing.T) {
	for _, args := range [][]string{
		{"install"}, {"install", "/srv/research", "--market-item", "market-1"},
		{"install", "--market-item", ""}, {"install", "/srv/research", "--on-conflict", "invalid"},
		{"preview", "http://"}, {"preview", "/srv/research", "-o", "csv"},
	} {
		t.Run(strings.Join(args, " "), func(t *testing.T) {
			f := useAgentPluginBackend(t)
			_, _, err := runCmd(t, Root(), append([]string{"plugin", "agent"}, args...)...)
			if err == nil || len(f.calls()) != 0 {
				t.Fatalf("invalid source: %v %+v", err, f.calls())
			}
		})
	}
	t.Run("oversize ZIP", func(t *testing.T) {
		f := useAgentPluginBackend(t)
		path := filepath.Join(t.TempDir(), "big.zip")
		file, err := os.Create(path)
		if err != nil {
			t.Fatal(err)
		}
		if err := file.Truncate(agentPluginMaxZipBytes + 1); err != nil {
			t.Fatal(err)
		}
		file.Close()
		_, _, err = runCmd(t, Root(), "plugin", "agent", "install", path, "--yes")
		if err == nil || len(f.calls()) != 0 {
			t.Fatalf("oversize: %v %+v", err, f.calls())
		}
	})
}

func TestAgentPluginUpdateAndToggleRequests(t *testing.T) {
	for _, tc := range []struct {
		verb  string
		extra []string
	}{
		{"update", []string{"--yes", "--on-conflict", "overwrite"}}, {"enable", nil}, {"disable", nil},
	} {
		t.Run(tc.verb, func(t *testing.T) {
			f := useAgentPluginBackend(t)
			args := append([]string{"plugin", "agent", tc.verb, "agent-1", "-o", "json"}, tc.extra...)
			out, _, err := runCmd(t, Root(), args...)
			if err != nil || !json.Valid([]byte(out)) {
				t.Fatalf("%s: %v %s", tc.verb, err, out)
			}
			calls := f.calls()
			if len(calls) != 1 || calls[0].Method != http.MethodPost || calls[0].Path != agentPluginAPI+"/agent-1/"+tc.verb {
				t.Fatalf("calls: %+v", calls)
			}
			if tc.verb == "update" && (len(calls[0].Body) != 1 || calls[0].Body["on_conflict"] != "overwrite") {
				t.Fatalf("update: %+v", calls[0])
			}
		})
	}
}

func TestAgentPluginUpdateAndUninstallDoNotMutateOnRejection(t *testing.T) {
	for _, verb := range []string{"update", "uninstall"} {
		t.Run(verb, func(t *testing.T) {
			f := useAgentPluginBackend(t)
			root := Root()
			root.SetIn(strings.NewReader("n\n"))
			out, stderr, err := runCmd(t, root, "plugin", "agent", verb, "agent-1", "-o", "json")
			if err == nil || len(f.calls()) != 0 || out != "" || !strings.Contains(stderr, "[y/N]") {
				t.Fatalf("rejected %s: %v %+v stdout=%s stderr=%s", verb, err, f.calls(), out, stderr)
			}
		})
	}
}

func TestAgentPluginUninstallPreservesSharedMembers(t *testing.T) {
	f := useAgentPluginBackend(t)
	out, _, err := runCmd(t, Root(), "plugin", "agent", "uninstall", "agent-1", "--yes")
	if err != nil || !strings.Contains(out, "removed: skill analysis") || !strings.Contains(out, "kept: connector quotes (referenced_by_other_plugin)") {
		t.Fatalf("uninstall: %v %s", err, out)
	}
	if calls := f.calls(); len(calls) != 1 || calls[0].Method != http.MethodDelete || calls[0].Path != agentPluginAPI+"/agent-1" {
		t.Fatalf("calls: %+v", calls)
	}
}

func TestAgentPluginExportPublishesAndDoesNotClobber(t *testing.T) {
	f := useAgentPluginBackend(t)
	path := filepath.Join(t.TempDir(), "research.zip")
	out, _, err := runCmd(t, Root(), "plugin", "agent", "export", "agent-1", path, "-o", "json")
	if err != nil || !json.Valid([]byte(out)) {
		t.Fatalf("export: %v %s", err, out)
	}
	data, err := os.ReadFile(path)
	if err != nil || string(data) != f.ExportBody {
		t.Fatalf("file: %v %q", err, data)
	}
	if calls := f.calls(); len(calls) != 1 || calls[0].Method != http.MethodGet || calls[0].Path != agentPluginAPI+"/agent-1/export" {
		t.Fatalf("calls: %+v", calls)
	}
	if err := os.WriteFile(path, []byte("existing"), 0600); err != nil {
		t.Fatal(err)
	}
	_, _, err = runCmd(t, Root(), "plugin", "agent", "export", "agent-1", path)
	if err == nil || len(f.calls()) != 1 {
		t.Fatalf("existing file: %v %+v", err, f.calls())
	}
	data, _ = os.ReadFile(path)
	if string(data) != "existing" {
		t.Fatal("existing file changed")
	}
	_, _, err = runCmd(t, Root(), "plugin", "agent", "export", "agent-1", path, "--force")
	if err != nil {
		t.Fatal(err)
	}
	data, _ = os.ReadFile(path)
	if string(data) != f.ExportBody {
		t.Fatal("force did not replace existing file")
	}
	files, _ := filepath.Glob(filepath.Join(filepath.Dir(path), ".valuz-agent-plugin-*"))
	if len(files) != 0 {
		t.Fatalf("temporary files leaked: %v", files)
	}
}

func TestAgentPluginExportErrorsDoNotCreateOrReplaceFile(t *testing.T) {
	for _, tc := range []struct {
		status int
		kind   errs.Kind
	}{
		{http.StatusForbidden, errs.KindAuth}, {http.StatusNotFound, errs.KindInternal},
	} {
		t.Run(fmt.Sprint(tc.status), func(t *testing.T) {
			f := useAgentPluginBackend(t)
			f.ExportStatus = tc.status
			dir := t.TempDir()
			path := filepath.Join(dir, "research.zip")
			out, _, err := runCmd(t, Root(), "plugin", "agent", "export", "agent-1", path)
			if err == nil || errs.KindOf(err) != tc.kind || out != "" {
				t.Fatalf("export error: %v %s", err, out)
			}
			if _, err := os.Stat(path); !os.IsNotExist(err) {
				t.Fatalf("created destination on error: %v", err)
			}
			entries, _ := os.ReadDir(dir)
			if len(entries) != 0 {
				t.Fatalf("partial exports: %v", entries)
			}
			if err := os.WriteFile(path, []byte("existing"), 0600); err != nil {
				t.Fatal(err)
			}
			_, _, err = runCmd(t, Root(), "plugin", "agent", "export", "agent-1", path, "--force")
			if err == nil {
				t.Fatal("force accepted backend error")
			}
			data, _ := os.ReadFile(path)
			if string(data) != "existing" {
				t.Fatal("replaced destination on error")
			}
		})
	}
}

func TestAgentPluginExportRejectsDestinationRace(t *testing.T) {
	f := useAgentPluginBackend(t)
	path := filepath.Join(t.TempDir(), "research.zip")
	f.DuringExport = func() {
		if err := os.WriteFile(path, []byte("raced"), 0600); err != nil {
			t.Error(err)
		}
	}
	_, _, err := runCmd(t, Root(), "plugin", "agent", "export", "agent-1", path)
	if err == nil {
		t.Fatal("replaced destination created during export")
	}
	data, _ := os.ReadFile(path)
	if string(data) != "raced" {
		t.Fatal("raced destination overwritten")
	}
	files, _ := filepath.Glob(filepath.Join(filepath.Dir(path), ".valuz-agent-plugin-*"))
	if len(files) != 0 {
		t.Fatalf("temporary files leaked: %v", files)
	}
}

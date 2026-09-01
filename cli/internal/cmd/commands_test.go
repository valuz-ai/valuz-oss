package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/auth"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/config"
)

// fakeCmdBackend serves the endpoints the command surface touches.
func fakeCmdBackend(t *testing.T) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()
	mux.HandleFunc("/v1/system/status", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("/v1/projects", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			writeJSON(t, w, map[string]any{"projects": []map[string]string{
				{"id": "proj-1", "name": "ws", "root_path": "/ws"},
			}})
		case http.MethodPost:
			var b map[string]string
			_ = json.NewDecoder(r.Body).Decode(&b)
			writeJSON(t, w, map[string]string{"id": "proj-new", "name": b["name"], "root_path": b["root_path"]})
		}
	})
	mux.HandleFunc("/v1/sessions", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, map[string]any{"sessions": []map[string]any{
			{"id": "s-1", "project_id": "proj-1", "status": "idle", "runtime_provider": "deepagents"},
		}})
	})
	mux.HandleFunc("/v1/sessions/s-1", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, map[string]any{"id": "s-1", "project_id": "proj-1", "status": "idle", "runtime_provider": "deepagents", "permission_mode": "default"})
	})
	mux.HandleFunc("/v1/tasks", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, map[string]any{"tasks": []map[string]string{
			{"id": "t-1", "status": "active", "lead_agent_slug": "lead", "title": "do x"},
		}})
	})
	mux.HandleFunc("/v1/tasks/t-1", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, map[string]any{
			"task":   map[string]string{"id": "t-1", "status": "completed", "lead_agent_slug": "lead", "title": "do x"},
			"runs":   []any{},
			"events": []any{},
		})
	})
	return httptest.NewServer(mux)
}

func writeJSON(t *testing.T, w http.ResponseWriter, v any) {
	t.Helper()
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}

// runCmd executes a full command chain through the root (so persistent
// flags like --backend-url and the product-shell pre-run run) with
// isolated output buffers.
func runCmd(t *testing.T, root *cobra.Command, args ...string) (string, string, error) {
	t.Helper()
	var out, errBuf bytes.Buffer
	root.SetArgs(args)
	root.SetOut(&out)
	root.SetErr(&errBuf)
	err := root.Execute()
	return out.String(), errBuf.String(), err
}

func TestCmdProjectListJSON(t *testing.T) {
	srv := fakeCmdBackend(t)
	defer srv.Close()
	t.Setenv("HOME", t.TempDir())
	t.Setenv("VALUZ_BACKEND_BASE_URL", srv.URL)

	root := Root()
	out, _, err := runCmd(t, root, "project", "list", "-o", "json")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	var items []map[string]string
	if err := json.Unmarshal([]byte(out), &items); err != nil {
		t.Fatalf("output not JSON: %v\n%s", err, out)
	}
	if len(items) != 1 || items[0]["id"] != "proj-1" {
		t.Fatalf("unexpected: %v", items)
	}
}

func TestCmdSessionListJSON(t *testing.T) {
	srv := fakeCmdBackend(t)
	defer srv.Close()
	t.Setenv("HOME", t.TempDir())
	t.Setenv("VALUZ_BACKEND_BASE_URL", srv.URL)

	out, _, err := runCmd(t, Root(), "session", "list", "-o", "json")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	var items []map[string]any
	if err := json.Unmarshal([]byte(out), &items); err != nil {
		t.Fatalf("not JSON: %v", err)
	}
	if items[0]["id"] != "s-1" {
		t.Fatalf("unexpected: %v", items[0])
	}
}

func TestCmdTaskWaitTerminal(t *testing.T) {
	srv := fakeCmdBackend(t)
	defer srv.Close()
	t.Setenv("HOME", t.TempDir())
	t.Setenv("VALUZ_BACKEND_BASE_URL", srv.URL)

	out, _, err := runCmd(t, Root(), "task", "wait", "t-1")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if !bytes.Contains([]byte(out), []byte("completed")) {
		t.Fatalf("expected completed, got %q", out)
	}
}

func TestEnvPinningRoundTrip(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	store := config.NewProfileStore(filepath.Join(home, ".valuz-oss", "profiles"))
	p := &config.Profile{Defaults: map[string]string{"ENV": "cloud", "BACKEND_URL_cloud": "https://c:9"}}
	if err := store.Save(p); err != nil {
		t.Fatalf("save: %v", err)
	}
	r := config.NewResolver(p)
	got, err := r.BackendURL("")
	if err != nil {
		t.Fatalf("resolve: %v", err)
	}
	if got != "https://c:9" {
		t.Fatalf("env pin = %q, want https://c:9", got)
	}
}

func TestResolveBearerConcurrentRefresh(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	// Seed an expired pair whose refresh hits a fake control plane.
	var mu sync.Mutex
	refreshed := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		refreshed++
		mu.Unlock()
		writeJSON(t, w, map[string]any{
			"access_token": "acc-new", "refresh_token": "ref-new", "expires_in": 3600,
			"principal": map[string]string{"master_id": "m", "distribution": "d"},
		})
	}))
	defer srv.Close()

	store := &auth.Store{Path: filepath.Join(home, ".valuz-oss", "auth.json")}
	if err := store.Save(&auth.TokenPair{
		AccessToken: "acc-old", RefreshToken: "ref-old",
		ExpiresAt: time.Now().Add(-time.Hour),
	}); err != nil {
		t.Fatalf("seed: %v", err)
	}

	opts := &RootOptions{CloudURL: srv.URL}
	var wg sync.WaitGroup
	for i := 0; i < 5; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := resolveBearer(opts)
			if err != nil {
				t.Errorf("resolveBearer: %v", err)
			}
		}()
	}
	wg.Wait()

	// The lock should collapse concurrent refreshes into one.
	mu.Lock()
	defer mu.Unlock()
	if refreshed > 2 {
		t.Fatalf("expected ~1 refresh under lock, got %d", refreshed)
	}
	// Store must hold the renewed pair.
	pair, err := store.Load()
	if err != nil || pair.AccessToken != "acc-new" {
		t.Fatalf("store not updated: %v %+v", err, pair)
	}
}

var _ = context.Background
var _ = fmt.Sprintf

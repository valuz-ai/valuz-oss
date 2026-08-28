package runner

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
)

// fakeBackend serves the endpoints the runner touches and streams a
// fixture as the SSE body.
func fakeBackend(t *testing.T, fixture string) *httptest.Server {
	return fakeBackendHang(t, fixture, false)
}

// interruptCount is set by fakeBackendHang to record interrupt calls.
var interruptCount atomic.Int32

func fakeBackendHang(t *testing.T, fixture string, hang bool) *httptest.Server {
	t.Helper()
	fixturePath := filepath.Join("..", "..", "testdata", "sse", fixture)

	mux := http.NewServeMux()
	mux.HandleFunc("/v1/system/status", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("/v1/projects", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			writeJSON(t, w, map[string]any{"projects": []map[string]string{
				{"id": "proj-1", "root_path": "/workspace"},
			}})
		case http.MethodPost:
			var body map[string]string
			_ = json.NewDecoder(r.Body).Decode(&body)
			writeJSON(t, w, map[string]string{"id": "proj-new", "name": body["name"], "root_path": body["root_path"]})
		}
	})
	mux.HandleFunc("/v1/sessions", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, map[string]any{"id": "sess-1", "project_id": "proj-1", "status": "created", "runtime_provider": "deepagents", "permission_mode": "default"})
	})
	mux.HandleFunc("/v1/sessions/sess-1/messages", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, map[string]any{"id": "sess-1", "status": "running"})
	})
	mux.HandleFunc("/v1/sessions/sess-1/interrupt", func(w http.ResponseWriter, r *http.Request) {
		interruptCount.Add(1)
		writeJSON(t, w, map[string]any{"id": "sess-1", "status": "idle"})
	})
	mux.HandleFunc("/v1/sessions/sess-1/events/stream", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		flusher, _ := w.(http.Flusher)
		f, err := os.Open(fixturePath)
		if err != nil {
			t.Errorf("open fixture: %v", err)
			return
		}
		defer f.Close()
		sc := bufio.NewScanner(f)
		sent := 0
		for sc.Scan() {
			line := sc.Text()
			if line == "" {
				continue
			}
			if hang && sent >= 2 {
				// hang mode: only the opening frames, never a terminal
				// event; keep the connection open so the client's
				// timeout must terminate the run.
				<-r.Context().Done()
				return
			}
			fmt.Fprintf(w, "data: %s\n\n", line)
			sent++
			if flusher != nil {
				flusher.Flush()
			}
		}
	})
	return httptest.NewServer(mux)
}

func writeJSON(t *testing.T, w http.ResponseWriter, v any) {
	t.Helper()
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}

func newRunner(srv *httptest.Server) *Runner {
	base := srv.URL
	return New(
		backend.NewControlClient(base, ""),
		backend.NewStreamClient(base, ""),
		&bytes.Buffer{},
	)
}

func TestRunSuccess(t *testing.T) {
	srv := fakeBackend(t, "success.jsonl")
	defer srv.Close()

	res, err := newRunner(srv).Run(context.Background(), Options{
		ProjectID: "proj-1",
		Prompt:    "fix the test",
		RunID:     "run-1",
	})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if res.Status != "completed" {
		t.Fatalf("status = %q, want completed (err=%q)", res.Status, res.Error)
	}
	if res.SessionID != "sess-1" || res.MessageID != "msg-0001" {
		t.Fatalf("ids: %+v", res)
	}
}

func TestRunErrorOrder(t *testing.T) {
	srv := fakeBackend(t, "error.jsonl")
	defer srv.Close()

	res, err := newRunner(srv).Run(context.Background(), Options{
		ProjectID: "proj-1",
		Prompt:    "refactor auth",
		RunID:     "run-2",
	})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if res.Status != "error" {
		t.Fatalf("status = %q, want error", res.Status)
	}
	if !strings.Contains(res.Error, "token limit") {
		t.Fatalf("error message = %q", res.Error)
	}
}

func TestRunTimeout(t *testing.T) {
	srv := fakeBackendHang(t, "success.jsonl", true) // stream hangs after the fixture
	defer srv.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()

	res, err := newRunner(srv).Run(ctx, Options{
		ProjectID: "proj-1",
		Prompt:    "deploy",
		RunID:     "run-3",
		Timeout:   200 * time.Millisecond,
	})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if res.Status != "timeout" {
		t.Fatalf("status = %q, want timeout", res.Status)
	}
}

func TestRunCwdResolvesExistingProject(t *testing.T) {
	srv := fakeBackend(t, "success.jsonl")
	defer srv.Close()

	res, err := newRunner(srv).Run(context.Background(), Options{
		Cwd:    "/workspace",
		Prompt: "hi",
		RunID:  "run-4",
	})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if res.ProjectID != "proj-1" {
		t.Fatalf("project = %q, want proj-1", res.ProjectID)
	}
}

func TestRunActionRequiredTriggersInterrupt(t *testing.T) {
	srv := fakeBackend(t, "requires-action.jsonl")
	defer srv.Close()
	interruptCount.Store(0)

	res, err := newRunner(srv).Run(context.Background(), Options{
		ProjectID: "proj-1",
		Prompt:    "deploy the service",
		RunID:     "run-ar",
	})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if res.Status != "action_required" {
		t.Fatalf("status = %q, want action_required", res.Status)
	}
	if interruptCount.Load() == 0 {
		t.Fatal("action_required must best-effort interrupt the backend")
	}
}

func TestRunRequiresPromptOrUsageError(t *testing.T) {
	srv := fakeBackend(t, "success.jsonl")
	defer srv.Close()

	_, err := newRunner(srv).Run(context.Background(), Options{ProjectID: "proj-1", RunID: "run-5"})
	if err == nil || !strings.Contains(err.Error(), "prompt") {
		t.Fatalf("want usage error, got %v", err)
	}

	// No project/cwd is now the quick-chat shape: resolves to chat-default.
	res, err := newRunner(srv).Run(context.Background(), Options{Prompt: "hi", RunID: "run-6"})
	if err != nil {
		t.Fatalf("quick chat should not error: %v", err)
	}
	if res.ProjectID != QuickChatProjectID {
		t.Fatalf("quick chat project = %q, want %q", res.ProjectID, QuickChatProjectID)
	}
}

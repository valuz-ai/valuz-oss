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
	"testing"
	"time"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/output"
)

// holdOpenBackend serves the fixture and KEEPS the SSE connection open
// after the last frame (real backend behavior: never closes on its own),
// plus a REST /events history endpoint for reconciliation tests.
func holdOpenBackend(t *testing.T, fixture string, history map[string]any) *httptest.Server {
	t.Helper()
	fixturePath := filepath.Join("..", "..", "testdata", "sse", fixture)

	mux := http.NewServeMux()
	mux.HandleFunc("/v1/system/status", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("/v1/projects", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, map[string]any{"projects": []map[string]string{
			{"id": "proj-1", "root_path": "/workspace"},
		}})
	})
	mux.HandleFunc("/v1/sessions", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, map[string]any{"id": "sess-1", "project_id": "proj-1", "status": "created", "runtime_provider": "deepagents", "permission_mode": "default"})
	})
	mux.HandleFunc("/v1/sessions/sess-1/messages", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, map[string]any{"id": "sess-1", "status": "running"})
	})
	mux.HandleFunc("/v1/sessions/sess-1/interrupt", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, map[string]any{"id": "sess-1", "status": "idle"})
	})
	mux.HandleFunc("/v1/sessions/sess-1/events", func(w http.ResponseWriter, r *http.Request) {
		if history != nil {
			writeJSON(t, w, history)
			return
		}
		writeJSON(t, w, map[string]any{"session_id": "sess-1", "items": []any{}})
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
		for sc.Scan() {
			line := sc.Text()
			if line == "" {
				continue
			}
			fmt.Fprintf(w, "data: %s\n\n", line)
			if flusher != nil {
				flusher.Flush()
			}
		}
		// Real backend behavior: never close on its own.
		<-r.Context().Done()
	})
	return httptest.NewServer(mux)
}

func newHoldingRunner(srv *httptest.Server, stdout *bytes.Buffer) *Runner {
	base := srv.URL
	return New(
		backend.NewControlClient(base, ""),
		backend.NewStreamClient(base, ""),
		stdout,
	)
}

// TestRunnerTerminatesOnTargetIdle is the regression test for the P0
// hang: a real backend never closes the SSE stream, so the run must end
// as soon as the target turn's idle arrives — not after reconnect
// retries or the idle watchdog.
func TestRunnerTerminatesOnTargetIdle(t *testing.T) {
	srv := holdOpenBackend(t, "success.jsonl", nil)
	defer srv.Close()

	start := time.Now()
	res, err := newHoldingRunner(srv, &bytes.Buffer{}).Run(context.Background(), Options{
		ProjectID: "proj-1",
		Prompt:    "fix the test",
		RunID:     "run-idle",
	})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	elapsed := time.Since(start)
	if res.Status != "completed" {
		t.Fatalf("status = %q, want completed", res.Status)
	}
	if elapsed > 5*time.Second {
		t.Fatalf("run took %s — the terminal idle must end the run immediately", elapsed)
	}
}

// TestRunnerDrainsUsageAfterIdle verifies the interrupt fixture's
// idle-then-usage tail order still captures usage into the result.
func TestRunnerDrainsUsageAfterIdle(t *testing.T) {
	srv := holdOpenBackend(t, "interrupt.jsonl", nil)
	defer srv.Close()

	res, err := newHoldingRunner(srv, &bytes.Buffer{}).Run(context.Background(), Options{
		ProjectID: "proj-1",
		Prompt:    "summarize",
		RunID:     "run-usage",
	})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if res.Status != "completed" {
		t.Fatalf("status = %q", res.Status)
	}
	if res.Usage.InputTokens != 1200 || res.Usage.OutputTokens != 80 {
		t.Fatalf("usage = %+v, want input=1200 output=80", res.Usage)
	}
}

// TestRunSignalInterruptsWithExit130 verifies the Signal wiring: a
// cancelled context with a recorded signal classifies the run as
// interrupted, and the exit-code path maps SIGINT to 130.
func TestRunSignalInterruptsWithExit130(t *testing.T) {
	// Stream that never emits a terminal idle: the run must classify as
	// interrupted when the context is cancelled with a recorded signal.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.Contains(r.URL.Path, "/events/stream"):
			w.Header().Set("Content-Type", "text/event-stream")
			w.WriteHeader(http.StatusOK)
			flusher, _ := w.(http.Flusher)
			fmt.Fprintf(w, "data: %s\n\n", sseFrame("msg-sig", "message.user", "long task"))
			if flusher != nil {
				flusher.Flush()
			}
			<-r.Context().Done()
		case strings.HasSuffix(r.URL.Path, "/sessions"):
			writeJSON(t, w, map[string]any{"id": "sess-1", "status": "created"})
		case strings.HasSuffix(r.URL.Path, "/messages"):
			writeJSON(t, w, map[string]any{"id": "sess-1", "status": "running"})
		case strings.HasSuffix(r.URL.Path, "/projects"):
			writeJSON(t, w, map[string]any{"projects": []map[string]string{{"id": "proj-1", "root_path": "/workspace"}}})
		case strings.HasSuffix(r.URL.Path, "/system/status"):
			writeJSON(t, w, map[string]string{"status": "ok"})
		default:
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()

	ctx, cancel := context.WithCancel(context.Background())
	r := newHoldingRunner(srv, &bytes.Buffer{})
	done := make(chan *Result, 1)
	errCh := make(chan error, 1)
	go func() {
		res, err := r.Run(ctx, Options{
			ProjectID: "proj-1",
			Prompt:    "long task",
			RunID:     "run-sig",
			Signal:    "SIGINT",
		})
		if err != nil {
			errCh <- err
			return
		}
		done <- res
	}()

	time.Sleep(50 * time.Millisecond)
	cancel()

	select {
	case res := <-done:
		if res.Status != "interrupted" {
			t.Fatalf("status = %q, want interrupted", res.Status)
		}
		if code := output.ExitCodeFor(res.Status); code != 130 {
			t.Fatalf("ExitCodeFor(interrupted) = %d, want 130", code)
		}
	case err := <-errCh:
		t.Fatalf("Run: %v", err)
	case <-time.After(5 * time.Second):
		t.Fatal("run did not return after context cancel")
	}
}

// TestReconcileReplaysHistory verifies the reconnect-exhausted path: the
// stream fails before terminal, the REST history contains the target
// turn's idle, and the run completes from history without panicking
// (regression for the nil-map bug).
func TestReconcileReplaysHistory(t *testing.T) {
	history := map[string]any{
		"session_id": "sess-1",
		"items": []map[string]any{
			{
				"seq": 1,
				"event": map[string]any{
					"event_type": "message.user",
					"payload":    map[string]string{"message_id": "msg-9", "text": "hi"},
				},
				"event_uid": "hist-uid-1",
			},
			{
				"seq": 2,
				"event": map[string]any{
					"event_type": "session.idle",
					"payload":    map[string]string{"message_id": "msg-9", "stop_reason": `{"type":"end_turn"}`},
				},
				"event_uid": "hist-uid-2",
			},
		},
	}
	// Stream that dies immediately without a terminal event.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.Contains(r.URL.Path, "/events/stream"):
			w.Header().Set("Content-Type", "text/event-stream")
			w.WriteHeader(http.StatusOK)
			flusher, _ := w.(http.Flusher)
			fmt.Fprintf(w, "data: %s\n\n", sseFrame("msg-9", "message.user", "hi"))
			if flusher != nil {
				flusher.Flush()
			}
			return // close: stream dies before terminal
		case strings.Contains(r.URL.Path, "/events"):
			writeJSON(t, w, history)
		case strings.HasSuffix(r.URL.Path, "/sessions"):
			writeJSON(t, w, map[string]any{"id": "sess-1", "status": "created"})
		case strings.HasSuffix(r.URL.Path, "/messages"):
			writeJSON(t, w, map[string]any{"id": "sess-1", "status": "running"})
		case strings.HasSuffix(r.URL.Path, "/projects"):
			writeJSON(t, w, map[string]any{"projects": []map[string]string{{"id": "proj-1", "root_path": "/workspace"}}})
		case strings.HasSuffix(r.URL.Path, "/system/status"):
			writeJSON(t, w, map[string]string{"status": "ok"})
		default:
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()

	r := New(backend.NewControlClient(srv.URL, ""), backend.NewStreamClient(srv.URL, ""), &bytes.Buffer{})
	res, err := r.Run(context.Background(), Options{
		ProjectID:            "proj-1",
		Prompt:               "hi",
		RunID:                "run-rec",
		ReconnectMaxAttempts: 1,
	})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if res.Status != "completed" {
		t.Fatalf("status = %q, want completed (reconciled from history)", res.Status)
	}
	if res.MessageID != "msg-9" {
		t.Fatalf("message id = %q", res.MessageID)
	}
}

func sseFrame(messageID, eventType, text string) string {
	raw, _ := json.Marshal(map[string]any{
		"seq": 1, "event_type": eventType,
		"payload":   map[string]string{"message_id": messageID, "text": text},
		"event_uid": "uid-" + messageID,
	})
	return string(raw)
}

// TestRunHumanOutputGated verifies stdout separation: machine protocols
// never receive plain-text deltas.
func TestRunHumanOutputGated(t *testing.T) {
	srv := holdOpenBackend(t, "success.jsonl", nil)
	defer srv.Close()

	var buf bytes.Buffer
	r := New(backend.NewControlClient(srv.URL, ""), backend.NewStreamClient(srv.URL, ""), &buf)
	if _, err := r.Run(context.Background(), Options{
		ProjectID:   "proj-1",
		Prompt:      "fix the test",
		RunID:       "run-gated",
		HumanOutput: false, // json/jsonl mode: no plain text on stdout
	}); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if buf.Len() != 0 {
		t.Fatalf("machine protocol leaked human text: %q", buf.String())
	}
}

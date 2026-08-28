package backend

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// reconnectServer closes the SSE body after delivering a prefix of the
// fixture, then serves the remainder on the next connection.
func reconnectServer(t *testing.T, firstChunk, secondChunk []string) *httptest.Server {
	t.Helper()
	var connections atomic.Int32

	mux := http.NewServeMux()
	mux.HandleFunc("/v1/sessions/sess-1/events/stream", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		flusher, _ := w.(http.Flusher)
		conn := connections.Add(1)

		var chunk []string
		if conn == 1 {
			chunk = firstChunk
		} else {
			chunk = secondChunk
			if got := r.URL.Query().Get("after_seq"); got == "" {
				t.Errorf("reconnect missing after_seq")
			}
		}
		for _, line := range chunk {
			fmt.Fprintf(w, "data: %s\n\n", line)
			if flusher != nil {
				flusher.Flush()
			}
		}
		if conn == 1 {
			// Drop the first connection so the client reconnects.
			time.Sleep(50 * time.Millisecond)
			return
		}
		// Second connection stays open; the client cancels on idle.
		<-r.Context().Done()
	})
	return httptest.NewServer(mux)
}

func sseLine(t *testing.T, seq int, eventType, messageID string) string {
	t.Helper()
	frame := map[string]any{
		"seq":        seq,
		"event_type": eventType,
		"payload":    map[string]string{"message_id": messageID, "text": "x"},
		"event_uid":  fmt.Sprintf("uid-%d", seq),
	}
	raw, _ := json.Marshal(frame)
	return string(raw)
}

func TestStreamReconnectResumesFromHeartbeatCursor(t *testing.T) {
	// Connection 1: user + delta + heartbeat(seq=9001), then drop.
	// Connection 2: after_seq must be 9001; serves the terminal idle.
	srv := reconnectServer(t,
		[]string{
			sseLine(t, 1001, "message.user", "msg-1"),
			sseLine(t, 1002, "message.assistant.delta", "msg-1"),
			`{"seq":9001,"event_type":null,"payload":{},"event_uid":null}`,
		},
		[]string{
			sseLine(t, 1003, "session.idle", "msg-1"),
		},
	)
	defer srv.Close()

	c := NewStreamClient(srv.URL, "")
	c.ReconnectMaxAttempts = 2
	c.ReconnectBaseDelay = 10 * time.Millisecond
	c.IdleDeadline = 0

	var mu sync.Mutex
	var events []string
	var reconnects int
	c.OnReconnect = func(afterSeq int64, attempt int) {
		mu.Lock()
		defer mu.Unlock()
		reconnects++
		if afterSeq != 9001 {
			t.Errorf("reconnect cursor = %d, want 9001", afterSeq)
		}
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	err := c.Stream(ctx, "sess-1", 0, func(_ context.Context, f *SSEFrame) error {
		mu.Lock()
		defer mu.Unlock()
		if f.IsHeartbeat() {
			return nil
		}
		events = append(events, *f.EventType)
		if *f.EventType == "session.idle" {
			// Terminal event seen: end the stream like a real run would.
			go cancel()
		}
		return nil
	})
	if err != nil {
		t.Fatalf("Stream: %v", err)
	}
	mu.Lock()
	defer mu.Unlock()
	if reconnects != 1 {
		t.Fatalf("reconnects = %d, want 1", reconnects)
	}
	if len(events) != 3 {
		t.Fatalf("events = %v, want 3", events)
	}
}

func TestStreamNoReconnectOnAuthError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
	}))
	defer srv.Close()

	c := NewStreamClient(srv.URL, "")
	c.ReconnectMaxAttempts = 5
	c.ReconnectBaseDelay = time.Millisecond
	var reconnects atomic.Int32
	c.OnReconnect = func(_ int64, _ int) { reconnects.Add(1) }

	err := c.Stream(context.Background(), "sess-1", 0, func(_ context.Context, _ *SSEFrame) error {
		return nil
	})
	if err == nil {
		t.Fatal("expected auth error")
	}
	if got := reconnects.Load(); got != 0 {
		t.Fatalf("reconnects = %d, want 0 (auth must not retry)", got)
	}
}

func TestStreamIdleDeadline(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		flusher, _ := w.(http.Flusher)
		fmt.Fprintf(w, "data: %s\n\n", sseLine(t, 1001, "message.user", "msg-1"))
		if flusher != nil {
			flusher.Flush()
		}
		<-r.Context().Done() // then silence forever
	}))
	defer srv.Close()

	c := NewStreamClient(srv.URL, "")
	c.IdleDeadline = 80 * time.Millisecond
	c.ReconnectMaxAttempts = 0

	err := c.Stream(context.Background(), "sess-1", 0, func(_ context.Context, _ *SSEFrame) error {
		return nil
	})
	if err == nil {
		t.Fatal("expected idle timeout error")
	}
}

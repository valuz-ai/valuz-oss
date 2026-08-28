package backend

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
)

// StreamClient consumes the long-lived session SSE stream
// (GET /v1/sessions/{id}/events/stream). It deliberately has no global
// timeout: lifecycle is owned by the run context, the heartbeat idle
// deadline and the reconnect policy (design.md §5.1).
type StreamClient struct {
	BaseURL string
	HTTP    *http.Client
	Token   string
	// ExtraHeaders mirror ControlClient (C10 client identity).
	ExtraHeaders map[string]string

	// ReconnectMaxAttempts caps reconnects per Stream call (0 = no reconnect).
	ReconnectMaxAttempts int
	// ReconnectBaseDelay is the first backoff delay (default 1s).
	ReconnectBaseDelay time.Duration
	// IdleDeadline aborts a connection that produced no frame (heartbeat
	// included) for this long (0 = disabled; backend heartbeats every 15s).
	IdleDeadline time.Duration
	// OnReconnect is called before each retry with the durable cursor.
	OnReconnect func(afterSeq int64, attempt int)
}

// ErrIdleTimeout signals that a connection went silent past IdleDeadline.
var ErrIdleTimeout = errors.New("SSE stream idle timeout (no heartbeat)")

// NewStreamClient builds an SSE client with Timeout=0.
func NewStreamClient(baseURL, token string) *StreamClient {
	return &StreamClient{
		BaseURL: strings.TrimRight(baseURL, "/"),
		HTTP:    &http.Client{Timeout: 0},
		Token:   token,
	}
}

// FrameHandler receives decoded frames. Returning an error aborts the
// stream (propagated from Stream).
type FrameHandler func(ctx context.Context, f *SSEFrame) error

// Stream opens the SSE stream and feeds decoded frames to handler until
// ctx is done, the server closes, or handler returns an error. When
// ReconnectMaxAttempts > 0 the connection is retried with the last
// durable heartbeat cursor (dual seq spaces: heartbeat frames carry the
// durable cursor; live frames carry kernel-local seq — see
// SessionEventFrame in OpenAPI).
func (s *StreamClient) Stream(ctx context.Context, sessionID string, afterSeq int64, handler FrameHandler) error {
	cursor := afterSeq
	attempt := 0
	for {
		err := s.streamOnce(ctx, sessionID, cursor, handler, &cursor)
		if err == nil || ctx.Err() != nil {
			return nil
		}
		if !s.reconnectAllowed(attempt, err) {
			return err
		}
		attempt++
		if s.OnReconnect != nil {
			s.OnReconnect(cursor, attempt)
		}
		delay := s.ReconnectBaseDelay
		if delay <= 0 {
			delay = time.Second
		}
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(delay):
		}
	}
}

// reconnectAllowed decides whether a failed connection is worth retrying:
// only transport/idle failures, never 4xx (auth) or handler errors.
func (s *StreamClient) reconnectAllowed(attempt int, err error) bool {
	if s.ReconnectMaxAttempts <= 0 || attempt >= s.ReconnectMaxAttempts {
		return false
	}
	var e *errs.Error
	if errors.As(err, &e) {
		if e.Kind == errs.KindAuth {
			return false // 401/403: never retry (design.md §7)
		}
		if e.Kind == errs.KindUsage {
			return false // 4xx: contract violation, not transient
		}
	}
	return true
}

// streamOnce runs a single connection. cursor is updated in place with
// the last durable heartbeat seq so a reconnect resumes from there.
func (s *StreamClient) streamOnce(ctx context.Context, sessionID string, afterSeq int64, handler FrameHandler, cursor *int64) error {
	path := fmt.Sprintf("/v1/sessions/%s/events/stream", sessionID)
	if afterSeq > 0 {
		path += fmt.Sprintf("?after_seq=%d", afterSeq)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, s.BaseURL+path, nil)
	if err != nil {
		return errs.Wrap(errs.KindInternal, err, "build SSE request")
	}
	if s.Token != "" {
		req.Header.Set("Authorization", "Bearer "+s.Token)
	}
	for k, v := range s.ExtraHeaders {
		req.Header.Set(k, v)
	}

	resp, err := s.HTTP.Do(req)
	if err != nil {
		if errors.Is(err, context.Canceled) {
			return nil // run ended; not an error
		}
		return errs.Wrap(errs.KindUnreachable, err, "open SSE stream")
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return errs.New(errorKindForStatus(resp.StatusCode), "SSE stream → HTTP %d", resp.StatusCode)
	}

	return s.consume(ctx, resp.Body, handler, cursor)
}

// consume reads SSE frames. Frame format (event_sse_adapter.go contract):
//
//	event: <legacy_type>\ndata: <flat SessionEventFrame JSON>\n
//
// Heartbeat frames carry only "seq" (no event_type) and are surfaced as
// frames with EventType == nil.
func (s *StreamClient) consume(ctx context.Context, body io.Reader, handler FrameHandler, cursor *int64) error {
	scanner := bufio.NewScanner(body)
	scanner.Buffer(make([]byte, 0, 64<<10), 4<<20) // 4 MiB max frame

	// The scanner blocks on Read; run it in a goroutine and select on
	// lines vs. the idle deadline so a silent connection is detectable.
	lines := make(chan string, 16)
	go func() {
		defer close(lines)
		for scanner.Scan() {
			select {
			case lines <- scanner.Text():
			case <-ctx.Done():
				return
			}
		}
	}()

	var dataBuf strings.Builder
	var idleTimer *time.Timer
	var idleCh <-chan time.Time

	resetIdle := func() {
		if s.IdleDeadline <= 0 {
			return
		}
		if idleTimer != nil {
			idleTimer.Stop()
		}
		idleTimer = time.NewTimer(s.IdleDeadline)
		idleCh = idleTimer.C
	}
	resetIdle()
	defer func() {
		if idleTimer != nil {
			idleTimer.Stop()
		}
	}()

	flush := func() error {
		if dataBuf.Len() == 0 {
			return nil
		}
		defer dataBuf.Reset()
		var f SSEFrame
		if err := json.Unmarshal([]byte(dataBuf.String()), &f); err != nil {
			return errs.Wrap(errs.KindInternal, err, "decode SSE frame")
		}
		// Heartbeat frames advance the durable reconnect cursor.
		if f.IsHeartbeat() && cursor != nil && f.Seq > 0 {
			*cursor = int64(f.Seq)
		}
		return handler(ctx, &f)
	}

	for {
		select {
		case <-ctx.Done():
			return nil // run ended
		case <-idleCh:
			return errs.Wrap(errs.KindInternal, ErrIdleTimeout, "SSE stream went silent")
		case line, ok := <-lines:
			if !ok {
				if err := scanner.Err(); err != nil && !errors.Is(err, io.EOF) {
					if ctx.Err() != nil {
						return nil
					}
					return errs.Wrap(errs.KindInternal, err, "read SSE stream")
				}
				if ctx.Err() != nil {
					return nil
				}
				// Clean server close: retryable transport error (the run
				// may not be finished; reconnect resumes from the cursor).
				return errs.New(errs.KindInternal, "SSE stream closed by server")
			}
			resetIdle()
			switch {
			case strings.HasPrefix(line, ":"):
				// comment; ignore
			case strings.HasPrefix(line, "data:"):
				data := strings.TrimPrefix(line, "data:")
				data = strings.TrimPrefix(data, " ")
				if dataBuf.Len() > 0 {
					dataBuf.WriteString("\n")
				}
				dataBuf.WriteString(data)
			case line == "":
				if err := flush(); err != nil {
					return err
				}
			}
		}
	}
}
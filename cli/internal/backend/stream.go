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
}

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
// ctx is done, the server closes, or handler returns an error.
// afterSeq replays durable history first (dual seq spaces: heartbeat
// frames carry the durable cursor; live frames carry kernel-local seq —
// see SessionEventFrame in OpenAPI).
func (s *StreamClient) Stream(ctx context.Context, sessionID string, afterSeq int64, handler FrameHandler) error {
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

	return s.consume(ctx, resp.Body, handler)
}

// consume reads SSE frames. Frame format (event_sse_adapter.go contract):
//
//	event: <legacy_type>\ndata: <flat SessionEventFrame JSON>\n
//
// Heartbeat frames carry only "seq" (no event_type) and are surfaced as
// frames with EventType == nil.
func (s *StreamClient) consume(ctx context.Context, body io.Reader, handler FrameHandler) error {
	scanner := bufio.NewScanner(body)
	scanner.Buffer(make([]byte, 0, 64<<10), 4<<20) // 4 MiB max frame

	var dataBuf strings.Builder

	flush := func() error {
		if dataBuf.Len() == 0 {
			return nil
		}
		defer dataBuf.Reset()
		var f SSEFrame
		if err := json.Unmarshal([]byte(dataBuf.String()), &f); err != nil {
			return errs.Wrap(errs.KindInternal, err, "decode SSE frame")
		}
		return handler(ctx, &f)
	}

	for scanner.Scan() {
		line := scanner.Text()
		if err := ctx.Err(); err != nil {
			return nil // run ended
		}
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
	if err := scanner.Err(); err != nil && !errors.Is(err, io.EOF) {
		if ctx.Err() != nil {
			return nil // cancelled mid-read
		}
		return errs.Wrap(errs.KindInternal, err, "read SSE stream")
	}
	return nil
}
package backend

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"

	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
)

// ControlClient issues bounded HTTP JSON requests against the backend.
// Unlike the legacy client.go wrapper it carries per-call context
// deadlines (design.md §5.1), parses every backend error shape into a
// typed error, and injects the bearer header via a shared transport.
type ControlClient struct {
	BaseURL string
	HTTP    *http.Client
	// Token is the optional bearer credential (Slice 5 wiring). Empty
	// means the OSS local-identity path.
	Token string
	// ExtraHeaders are client-identity/capability headers (C10 contract);
	// reserved for the negotiation slice.
	ExtraHeaders map[string]string
}

// NewControlClient builds a client with a bounded default timeout; callers
// may set per-call timeouts via context.
func NewControlClient(baseURL, token string) *ControlClient {
	return &ControlClient{
		BaseURL: strings.TrimRight(baseURL, "/"),
		HTTP:    &http.Client{Timeout: 30 * time.Second},
		Token:   token,
	}
}

// Get issues a GET decoding JSON into out.
func (c *ControlClient) Get(ctx context.Context, path string, out any) error {
	return c.do(ctx, http.MethodGet, path, nil, out)
}

// Post issues a POST with an optional JSON body.
func (c *ControlClient) Post(ctx context.Context, path string, body, out any) error {
	return c.do(ctx, http.MethodPost, path, body, out)
}

// Put issues a PUT with an optional JSON body.
func (c *ControlClient) Put(ctx context.Context, path string, body, out any) error {
	return c.do(ctx, http.MethodPut, path, body, out)
}

// do is the single request path: it classifies transport errors, parses
// the known backend error bodies ({error:{...}} and {detail:...}) and
// wraps everything in a typed CLI error.
func (c *ControlClient) do(ctx context.Context, method, path string, body, out any) error {
	var reqBody io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			return errs.Wrap(errs.KindUsage, err, "encode request body")
		}
		reqBody = bytes.NewReader(raw)
	}

	req, err := http.NewRequestWithContext(ctx, method, c.BaseURL+path, reqBody)
	if err != nil {
		return errs.Wrap(errs.KindInternal, err, "build %s %s request", method, path)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if c.Token != "" {
		req.Header.Set("Authorization", "Bearer "+c.Token)
	}
	for k, v := range c.ExtraHeaders {
		req.Header.Set(k, v)
	}

	resp, err := c.HTTP.Do(req)
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			return errs.New(errs.KindTimeout, "%s %s timed out", method, path)
		}
		return errs.Wrap(errs.KindUnreachable, err, "could not reach backend at %s", c.BaseURL)
	}
	defer resp.Body.Close()

	if resp.StatusCode < 400 {
		if out == nil {
			_, _ = io.Copy(io.Discard, resp.Body)
			return nil
		}
		raw, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
		if err != nil {
			return errs.Wrap(errs.KindInternal, err, "read %s response", path)
		}
		if len(raw) == 0 {
			return nil
		}
		if err := json.Unmarshal(raw, out); err != nil {
			return errs.Wrap(errs.KindInternal, err, "decode %s response", path)
		}
		return nil
	}

	return c.classifyError(resp, method, path)
}

// classifyError parses the four known backend error shapes and maps them
// to typed CLI errors (design.md §5.2, research §2.5).
func (c *ControlClient) classifyError(resp *http.Response, method, path string) error {
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 16<<10))
	body := strings.TrimSpace(string(raw))

	var ve ValuzError
	if json.Unmarshal(raw, &ve) == nil && ve.Error.Message != "" {
		return errs.Wrap(errorKindForStatus(resp.StatusCode), nil,
			"backend %s %s: %s", method, path, errs.Redact(ve.Error.Message))
	}
	var de DetailError
	if json.Unmarshal(raw, &de) == nil {
		switch d := de.Detail.(type) {
		case string:
			if d != "" {
				return errs.Wrap(errorKindForStatus(resp.StatusCode), nil,
					"backend %s %s: %s", method, path, errs.Redact(d))
			}
		case []any:
			// FastAPI 422: {"detail": [{"loc": ..., "msg": ..., "type": ...}]}
			msgs := make([]string, 0, len(d))
			for _, item := range d {
				if obj, ok := item.(map[string]any); ok {
					if msg, ok := obj["msg"].(string); ok && msg != "" {
						msgs = append(msgs, msg)
					}
				}
			}
			if len(msgs) > 0 {
				return errs.Wrap(errorKindForStatus(resp.StatusCode), nil,
					"backend %s %s: %s", method, path, errs.Redact(strings.Join(msgs, "; ")))
			}
		}
	}
	if body == "" {
		return errs.New(errorKindForStatus(resp.StatusCode), "%s %s → HTTP %d", method, path, resp.StatusCode)
	}
	return errs.New(errorKindForStatus(resp.StatusCode), "%s %s → HTTP %d: %s", method, path, resp.StatusCode, errs.Redact(body))
}

func errorKindForStatus(status int) errs.Kind {
	switch {
	case status == http.StatusUnauthorized || status == http.StatusForbidden:
		return errs.KindAuth
	case status >= 400 && status < 500:
		return errs.KindUsage
	default:
		return errs.KindInternal
	}
}

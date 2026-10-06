package backend

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/textproto"
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

// Delete issues a DELETE decoding the (optional) JSON response into out.
func (c *ControlClient) Delete(ctx context.Context, path string, out any) error {
	return c.do(ctx, http.MethodDelete, path, nil, out)
}

// MultipartField is one form field; a name may repeat (e.g. list values).
type MultipartField struct {
	Name  string
	Value string
}

// MultipartFile is the single file part of a multipart upload.
type MultipartFile struct {
	Field       string
	FileName    string
	ContentType string
	Data        []byte
}

// uploadTimeout bounds multipart uploads (packages can be a few MB on a
// slow link; the 30s request default is too tight).
const uploadTimeout = 5 * time.Minute

// PostMultipart issues a multipart/form-data POST: the fields in order,
// then the file part.
func (c *ControlClient) PostMultipart(ctx context.Context, path string, fields []MultipartField, file MultipartFile, out any) error {
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	for _, f := range fields {
		if err := mw.WriteField(f.Name, f.Value); err != nil {
			return errs.Wrap(errs.KindInternal, err, "encode form field %s", f.Name)
		}
	}
	header := make(textproto.MIMEHeader)
	header.Set("Content-Disposition", fmt.Sprintf(`form-data; name="%s"; filename="%s"`,
		quoteEscaper.Replace(file.Field), quoteEscaper.Replace(file.FileName)))
	contentType := file.ContentType
	if contentType == "" {
		contentType = "application/octet-stream"
	}
	header.Set("Content-Type", contentType)
	part, err := mw.CreatePart(header)
	if err != nil {
		return errs.Wrap(errs.KindInternal, err, "encode file part")
	}
	if _, err := part.Write(file.Data); err != nil {
		return errs.Wrap(errs.KindInternal, err, "encode file part")
	}
	if err := mw.Close(); err != nil {
		return errs.Wrap(errs.KindInternal, err, "encode multipart body")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+path, &buf)
	if err != nil {
		return errs.Wrap(errs.KindInternal, err, "build POST %s request", path)
	}
	req.Header.Set("Content-Type", mw.FormDataContentType())
	httpClient := c.HTTP
	if httpClient.Timeout > 0 && httpClient.Timeout < uploadTimeout {
		clone := *httpClient
		clone.Timeout = uploadTimeout
		httpClient = &clone
	}
	return c.send(httpClient, req, path, out)
}

var quoteEscaper = strings.NewReplacer("\\", "\\\\", `"`, "\\\"")

// do is the single JSON request path: it classifies transport errors,
// parses the known backend error bodies ({error:{...}} and {detail:...})
// and wraps everything in a typed CLI error.
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
	return c.send(c.HTTP, req, path, out)
}

// send adds the auth/identity headers, performs req and decodes the JSON
// response into out (nil = discard) or classifies the error body.
func (c *ControlClient) send(httpClient *http.Client, req *http.Request, path string, out any) error {
	method := req.Method
	if c.Token != "" {
		req.Header.Set("Authorization", "Bearer "+c.Token)
	}
	for k, v := range c.ExtraHeaders {
		req.Header.Set(k, v)
	}

	resp, err := httpClient.Do(req)
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

// classifyError parses the known backend error shapes and maps them to
// typed CLI errors (design.md §5.2, research §2.5):
//
//	{"error": {"code", "message", "errors"?}}   OSS ValuzError
//	{"error": "text"}                           OSS unhandled 500
//	{"detail": "text"}                          FastAPI HTTPException
//	{"detail": [{"msg", ...}]}                  FastAPI 422
//	{"detail": {"error": {"code", "message"}}}  control-plane HTTPException
//
// A list of validation findings ("errors") is appended to the message.
func (c *ControlClient) classifyError(resp *http.Response, method, path string) error {
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 16<<10))
	body := strings.TrimSpace(string(raw))

	var envelope map[string]any
	if json.Unmarshal(raw, &envelope) == nil {
		if msg := errorBodyMessage(envelope); msg != "" {
			return errs.Wrap(errorKindForStatus(resp.StatusCode), nil,
				"backend %s %s -> HTTP %d: %s", method, path, resp.StatusCode, errs.Redact(msg))
		}
	}
	if body == "" {
		return errs.New(errorKindForStatus(resp.StatusCode), "%s %s -> HTTP %d", method, path, resp.StatusCode)
	}
	return errs.New(errorKindForStatus(resp.StatusCode), "%s %s -> HTTP %d: %s", method, path, resp.StatusCode, errs.Redact(body))
}

// errorBodyMessage extracts the human message of an error envelope ("" when
// the shape is unknown).
func errorBodyMessage(m map[string]any) string {
	switch e := m["error"].(type) {
	case map[string]any:
		if msg, _ := e["message"].(string); msg != "" {
			return withDetails(msg, e["errors"])
		}
	case string:
		if e != "" {
			return e
		}
	}
	switch d := m["detail"].(type) {
	case string:
		if d != "" {
			return d
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
			return strings.Join(msgs, "; ")
		}
	case map[string]any:
		if msg := errorBodyMessage(d); msg != "" {
			return msg
		}
	}
	if msg, _ := m["message"].(string); msg != "" {
		return withDetails(msg, m["errors"])
	}
	return ""
}

// withDetails appends a findings list (strings or {path?, message}) to msg.
func withDetails(msg string, details any) string {
	list, ok := details.([]any)
	if !ok || len(list) == 0 {
		return msg
	}
	parts := make([]string, 0, len(list))
	for _, item := range list {
		switch v := item.(type) {
		case string:
			parts = append(parts, v)
		case map[string]any:
			text, _ := v["message"].(string)
			if p, _ := v["path"].(string); p != "" && text != "" {
				text = p + ": " + text
			}
			if text != "" {
				parts = append(parts, text)
			}
		}
	}
	if len(parts) == 0 {
		return msg
	}
	return msg + ": " + strings.Join(parts, "; ")
}

func errorKindForStatus(status int) errs.Kind {
	switch {
	case status == http.StatusUnauthorized || status == http.StatusForbidden:
		return errs.KindAuth
	case status == http.StatusNotFound:
		// The endpoint/resource does not exist server-side — an internal
		// mismatch (client and server versions), not a caller usage error.
		return errs.KindInternal
	case status >= 400 && status < 500:
		// 409/422/400: the caller's request/params are rejected by the
		// server contract — usage.
		return errs.KindUsage
	default:
		return errs.KindInternal
	}
}

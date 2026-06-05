// Package backend talks to the running Valuz backend HTTP API.
//
// Used by every Go CLI subcommand that needs to read/write live state
// (status, doctor probes, etc.). The base URL is resolved from
// VALUZ_BACKEND_BASE_URL with a localhost default — see resolveBaseURL.
package backend

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	runtimepkg "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/runtime"
)

// BaseURL returns the configured backend base URL with no trailing slash.
//
// Order:
//  1. “VALUZ_BACKEND_BASE_URL“ — explicit override (canonical)
//  2. Bundle mode (running inside Valuz.app) → :19100, the
//     PERSONAL_PORTS.AGENT_SERVER port the Electron sidecar binds
//  3. Dev mode → :8000 (backend/valuz_agent/main.py default)
func BaseURL() string {
	if v, ok := os.LookupEnv("VALUZ_BACKEND_BASE_URL"); ok && v != "" {
		return strings.TrimRight(v, "/")
	}
	if paths, err := runtimepkg.Discover(); err == nil && paths.BackendPort > 0 {
		return fmt.Sprintf("http://127.0.0.1:%d", paths.BackendPort)
	}
	return "http://127.0.0.1:8000"
}

// Client is a thin HTTP wrapper over BaseURL.
type Client struct {
	HTTP    *http.Client
	BaseURL string
}

// New returns a Client configured with sensible per-call timeouts.
func New() *Client {
	return &Client{
		HTTP:    &http.Client{Timeout: 15 * time.Second},
		BaseURL: BaseURL(),
	}
}

// Get issues a GET, decoding JSON into out (out may be nil to discard body).
func (c *Client) Get(path string, out any) error {
	return c.do("GET", path, nil, out)
}

// Post issues a POST with optional JSON body and decoded response.
func (c *Client) Post(path string, body, out any) error {
	return c.do("POST", path, body, out)
}

// Delete issues a DELETE and discards the response body.
func (c *Client) Delete(path string) error {
	return c.do("DELETE", path, nil, nil)
}

func (c *Client) do(method, path string, body, out any) error {
	url := c.BaseURL + path
	var reqBody io.Reader
	if body != nil {
		buf, err := json.Marshal(body)
		if err != nil {
			return fmt.Errorf("encode body: %w", err)
		}
		reqBody = bytes.NewReader(buf)
	}
	req, err := http.NewRequest(method, url, reqBody)
	if err != nil {
		return err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return fmt.Errorf("could not reach backend at %s: %w", c.BaseURL, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return fmt.Errorf("%s %s → HTTP %d: %s", method, path, resp.StatusCode, strings.TrimSpace(string(raw)))
	}
	if out == nil {
		_, _ = io.Copy(io.Discard, resp.Body)
		return nil
	}
	if resp.ContentLength == 0 {
		return nil
	}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil && err != io.EOF {
		return fmt.Errorf("decode %s: %w", path, err)
	}
	return nil
}

package backend

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
)

func TestControlDownloadUsesAuthAndIdentityHeaders(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.URL.Path != "/plugin/export" || r.Header.Get("Authorization") != "Bearer export-token" || r.Header.Get("X-Valuz-Version") != "test" {
			t.Errorf("request: %s %s %v", r.Method, r.URL.Path, r.Header)
		}
		fmt.Fprint(w, "PK archive")
	}))
	defer srv.Close()
	c := NewControlClient(srv.URL, "export-token")
	c.ExtraHeaders = map[string]string{"X-Valuz-Version": "test"}
	var out bytes.Buffer
	n, err := c.Download(context.Background(), "/plugin/export", &out, 32)
	if err != nil || n != 10 || out.String() != "PK archive" {
		t.Fatalf("download: %d %v %q", n, err, out.String())
	}
}

func TestControlDownloadRejectsSizedAndChunkedOversize(t *testing.T) {
	for _, chunked := range []bool{false, true} {
		t.Run(fmt.Sprint(chunked), func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if chunked {
					w.WriteHeader(http.StatusOK)
					w.(http.Flusher).Flush()
				}
				fmt.Fprint(w, strings.Repeat("x", 64))
			}))
			defer srv.Close()
			var out bytes.Buffer
			n, err := NewControlClient(srv.URL, "").Download(context.Background(), "/export", &out, 32)
			if err == nil || errs.KindOf(err) != errs.KindUsage || n > 33 || out.Len() > 33 {
				t.Fatalf("oversize: n=%d %v bytes=%d", n, err, out.Len())
			}
		})
	}
}

func TestControlDownloadClassifiesBackendErrorsWithoutWritingBody(t *testing.T) {
	for _, tc := range []struct {
		status int
		kind   errs.Kind
	}{{403, errs.KindAuth}, {404, errs.KindInternal}, {409, errs.KindUsage}} {
		t.Run(fmt.Sprint(tc.status), func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(tc.status)
				fmt.Fprint(w, `{"detail":"plugin refused"}`)
			}))
			defer srv.Close()
			var out bytes.Buffer
			n, err := NewControlClient(srv.URL, "").Download(context.Background(), "/export", &out, 32)
			if err == nil || errs.KindOf(err) != tc.kind || !strings.Contains(err.Error(), "plugin refused") || n != 0 || out.Len() != 0 {
				t.Fatalf("error: %v %d %q", err, n, out.String())
			}
		})
	}
}

func TestControlDownloadDetectsTruncatedResponseAndTimeout(t *testing.T) {
	t.Run("truncated", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Length", "20")
			fmt.Fprint(w, "PK")
		}))
		defer srv.Close()
		var out bytes.Buffer
		_, err := NewControlClient(srv.URL, "").Download(context.Background(), "/export", &out, 32)
		if err == nil {
			t.Fatal("truncated response accepted")
		}
	})
	t.Run("timeout", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { <-r.Context().Done() }))
		defer srv.Close()
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
		defer cancel()
		var out bytes.Buffer
		_, err := NewControlClient(srv.URL, "").Download(ctx, "/export", &out, 32)
		if err == nil || errs.KindOf(err) != errs.KindTimeout {
			t.Fatalf("timeout: %v", err)
		}
	})
}

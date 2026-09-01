package cmd

import (
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/version"
)

// clientHeaders are the C10 client-identity headers attached to every
// request (version/schemas/capabilities) so the backend can observe the
// CLI's capability surface.
func clientHeaders() map[string]string {
	return version.Current(true).Headers()
}

// newControlClient builds a bounded control client with identity headers.
func newControlClient(opts *RootOptions, token string) *backend.ControlClient {
	c := backend.NewControlClient(opts.BackendURL, token)
	c.ExtraHeaders = clientHeaders()
	return c
}

// newStreamClient builds an SSE client with identity headers.
func newStreamClient(opts *RootOptions, token string) *backend.StreamClient {
	c := backend.NewStreamClient(opts.BackendURL, token)
	c.ExtraHeaders = clientHeaders()
	return c
}

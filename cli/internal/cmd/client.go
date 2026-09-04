package cmd

import (
	"encoding/json"
	"fmt"
	"io"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
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

// printJSONOutput renders v as indented JSON when output == "json" and
// reports whether it consumed the output (human callers skip their table).
func printJSONOutput(out io.Writer, output string, v any) bool {
	if output != "json" {
		return false
	}
	raw, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		fmt.Fprintln(out, "json encode error:", err)
		return true
	}
	fmt.Fprintln(out, string(raw))
	return true
}

// checkOutputFormat rejects unknown --output values on list/detail
// commands (human|json), matching the run path's strict validation.
func checkOutputFormat(output string) error {
	switch output {
	case "", "human", "json":
		return nil
	default:
		return errs.New(errs.KindUsage, "unsupported --output %q (want human|json)", output)
	}
}

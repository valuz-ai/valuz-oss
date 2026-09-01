// Package version provides the version/capability JSON contract shared by
// every command (design.md §4.1). The CLI advertises its version, build
// identity and the output schemas it understands so callers — scripts, the
// eval harness, future supervisors — can negotiate compatibility without
// parsing help text.
package version

import (
	"encoding/json"
	"fmt"
	"runtime"
	"strings"
)

// Build-time injected values (via -ldflags in main.go).
var (
	Version   = "dev"
	Commit    = "unknown"
	BuildTime = "unknown"
)

// Output schemas this build understands. Bump on incompatible change;
// callers gate on presence rather than the CLI version string.
const (
	RunResultSchema = "valuz.run-result/v1"
	RunEventSchema  = "valuz.run-event/v1"
)

// Capabilities advertises optional behaviors for compatibility checks.
type Capabilities struct {
	HeadlessRun bool `json:"headless_run"`
}

// Info is the full version JSON document.
type Info struct {
	Version       string       `json:"version"`
	Commit        string       `json:"commit"`
	BuildTime     string       `json:"build_time"`
	Go            string       `json:"go"`
	OS            string       `json:"os"`
	Arch          string       `json:"arch"`
	OutputSchemas []string     `json:"output_schemas"`
	Capabilities  Capabilities `json:"capabilities"`
}

// Current assembles the Info for this build. headlessRun reflects whether
// the run command was compiled in (always true in the unified binary).
func Current(headlessRun bool) Info {
	return Info{
		Version:   Version,
		Commit:    Commit,
		BuildTime: BuildTime,
		Go:        runtime.Version(),
		OS:        runtime.GOOS,
		Arch:      runtime.GOARCH,
		OutputSchemas: []string{
			RunResultSchema,
			RunEventSchema,
		},
		Capabilities: Capabilities{HeadlessRun: headlessRun},
	}
}

// JSON renders the info document.
func (i Info) JSON() ([]byte, error) {
	return json.MarshalIndent(i, "", "  ")
}

// String renders the compact human form (version [+commit]).
func (i Info) String() string {
	if i.Commit == "unknown" || i.Commit == "" {
		return i.Version
	}
	return fmt.Sprintf("%s (%s)", i.Version, i.Commit)
}

// Headers renders the client-identity headers (C10 negotiation contract).
// Every HTTP call carries them so the backend can detect version drift and
// gate on capability/schema presence instead of parsing help text. Header
// names are provisional until the OSS contract (C10) pins them.
func (i Info) Headers() map[string]string {
	return map[string]string{
		"X-Valuz-Client-Version":      i.Version,
		"X-Valuz-Client-Commit":       i.Commit,
		"X-Valuz-Client-Schemas":      strings.Join(i.OutputSchemas, ","),
		"X-Valuz-Client-Capabilities": strings.Join([]string{"headless_run"}, ","),
	}
}

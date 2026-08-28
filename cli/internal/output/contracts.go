// Package output implements the versioned CLI output contracts
// (design.md §6): the RunResult document (valuz.run-result/v1), the
// RunEvent JSONL envelope (valuz.run-event/v1) and the event-level
// redaction/truncation policy applied before anything is written to
// stdout, debug traces or trajectory files.
package output

import (
	"encoding/json"
	"strings"

	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
)

// Status values (design.md §6.1).
const (
	StatusCompleted      = "completed"
	StatusError          = "error"
	StatusTimeout        = "timeout"
	StatusInterrupted    = "interrupted"
	StatusActionRequired = "action_required"
	StatusAuthError      = "auth_error"
	StatusInternalError  = "internal_error"
)

// ExitCodeFor maps a status to its shell exit code (design.md §6.3).
// Interrupted runs follow the Unix signal convention (128+2).
func ExitCodeFor(status string) int {
	switch status {
	case StatusCompleted:
		return 0
	case StatusTimeout:
		return 2
	case StatusError:
		return 3
	case StatusActionRequired:
		return 7
	case StatusAuthError:
		return 6
	case StatusInternalError:
		return 5
	case StatusInterrupted:
		return 130
	default:
		return 1
	}
}

// Usage is the four-bucket token accounting.
type Usage struct {
	InputTokens      int64 `json:"input_tokens"`
	OutputTokens     int64 `json:"output_tokens"`
	CacheReadTokens  int64 `json:"cache_read_tokens"`
	CacheWriteTokens int64 `json:"cache_write_tokens"`
}

// RunResult is the terminal result document (valuz.run-result/v1).
type RunResult struct {
	SchemaVersion string `json:"schema_version"`
	RunID         string `json:"run_id"`
	SessionID     string `json:"session_id"`
	ProjectID     string `json:"project_id"`
	MessageID     string `json:"message_id,omitempty"`
	AgentSlug     string `json:"agent_slug,omitempty"`
	Runtime       string `json:"runtime,omitempty"`
	Model         string `json:"model,omitempty"`
	Status        string `json:"status"`
	ExitCode      int    `json:"exit_code"`
	StartedAt     string `json:"started_at"`
	FinishedAt    string `json:"finished_at"`
	DurationMS    int64  `json:"duration_ms"`
	Usage         Usage  `json:"usage"`
	NumTurns      int    `json:"num_turns"`
	FinalMessage  string `json:"final_message,omitempty"`
	Error         string `json:"error,omitempty"`
}

// Marshal renders the document as indented JSON.
func (r RunResult) Marshal() ([]byte, error) {
	return json.MarshalIndent(r, "", "  ")
}

// Event source labels (design.md §6.2).
type Source string

const (
	SourceLive    Source = "live"
	SourceHistory Source = "history"
	SourceCLI     Source = "cli"
)

// RunEvent is one JSONL line of the event stream (valuz.run-event/v1).
type RunEvent struct {
	SchemaVersion string         `json:"schema_version"`
	RunID         string         `json:"run_id"`
	SessionID     string         `json:"session_id"`
	ProjectID     string         `json:"project_id"`
	MessageID     string         `json:"message_id,omitempty"`
	EventUID      string         `json:"event_uid,omitempty"`
	Source        Source         `json:"source"`
	SourceSeq     *int64         `json:"source_seq"`
	Timestamp     string         `json:"timestamp,omitempty"`
	Type          string         `json:"type"`
	Data          map[string]any `json:"data"`
}

// RunEndType is the CLI-synthesized terminal event type.
const RunEndType = "run.end"

// Marshal renders one event line (compact JSON + newline).
func (e RunEvent) Marshal() ([]byte, error) {
	return json.Marshal(e)
}

// ── redaction / truncation policy (applied before any write) ───────

// RedactPolicy controls event-level scrubbing. Secret-like fields are
// masked; oversized tool input/output keep their structure and length
// with a truncated=true marker (never silently cut).
type RedactPolicy struct {
	// MaxFieldBytes caps a single string value before truncation.
	MaxFieldBytes int
	// MaxDataBytes caps the whole data map's serialized size.
	MaxDataBytes int
}

// DefaultRedactPolicy is the standard policy (1 MiB per field, 4 MiB per
// event).
func DefaultRedactPolicy() RedactPolicy {
	return RedactPolicy{MaxFieldBytes: 1 << 20, MaxDataBytes: 4 << 20}
}

// RedactField scrubs a string value. Applies the shared secret matcher
// and, when over the cap, truncates with the marker preserved.
func (p RedactPolicy) RedactField(v string) string {
	v = errs.Redact(v)
	if p.MaxFieldBytes > 0 && len(v) > p.MaxFieldBytes {
		return v[:p.MaxFieldBytes] + "…[truncated=true]"
	}
	return v
}

// RedactData applies the policy to every string value in data.
func (p RedactPolicy) RedactData(data map[string]any) map[string]any {
	out := make(map[string]any, len(data))
	for k, v := range data {
		if s, ok := v.(string); ok {
			out[k] = p.RedactField(s)
			continue
		}
		out[k] = v
	}
	if p.MaxDataBytes > 0 {
		if raw, err := json.Marshal(out); err == nil && len(raw) > p.MaxDataBytes {
			out = map[string]any{"truncated": true, "bytes": len(raw)}
		}
	}
	return out
}

// NormalizeNewlines collapses \r\n and trims trailing whitespace of text
// fragments emitted as event data (keeps JSONL lines intact).
func NormalizeNewlines(s string) string {
	s = strings.ReplaceAll(s, "\r\n", "\n")
	s = strings.ReplaceAll(s, "\r", "\n")
	return strings.TrimRight(s, "\n")
}

// Package event maps wire SSE frames to the CLI's RunEvent shape
// (design.md §6.2). Only fields the mapping table declares are decoded
// from the stringified payload; no heuristic conversion of other strings.
package event

import (
	"encoding/json"
	"strconv"
	"strings"
)

// Source distinguishes live frames, history replay and CLI-synthesized
// events in the JSONL output.
type Source string

const (
	SourceLive    Source = "live"
	SourceHistory Source = "history"
	SourceCLI     Source = "cli"
)

// RunEvent is one line of the JSONL event stream (valuz.run-event/v1).
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

// Mapper converts frames into typed payloads. The run path consumes the
// typed payloads; the JSONL renderer emits RunEvents.
type Mapper struct{}

// New returns a mapper.
func New() *Mapper { return &Mapper{} }

// Payload types the mapper recognizes (subset of the legacy event names).
const (
	EventUser           = "message.user"
	EventAssistantDelta = "message.assistant.delta"
	EventAssistantText  = "message.assistant.text_delta"
	EventThinking       = "message.assistant.thinking"
	EventToolStarted    = "tool.call.started"
	EventToolCompleted  = "tool.call.completed"
	EventRunFailed      = "run.failed"
	EventUsage          = "runtime.engine.usage"
	EventIdle           = "session.idle"
	EventRequiresAction = "session.requires_action"
	EventTodosUpdate    = "session.todos.update"
)

// UserMessage is the decoded message.user payload.
type UserMessage struct {
	Text string
}

// AssistantDelta is the decoded message.assistant.delta payload.
type AssistantDelta struct {
	Text string
}

// ToolCall is the decoded tool.call.* payload.
type ToolCall struct {
	ID      string
	Name    string
	Input   string
	Content string
	IsError bool
}

// RunError is the decoded run.failed payload.
type RunError struct {
	Message  string
	Category string
}

// Idle is the decoded session.idle payload.
type Idle struct {
	StopReason string
}

// Usage is the decoded runtime.engine.usage payload.
type Usage struct {
	InputTokens     int64
	OutputTokens    int64
	CacheReadTokens int64
	CacheWriteTokens int64
}

// RequiresAction is the decoded session.requires_action payload.
type RequiresAction struct {
	PendingID  string
	ToolUseID  string
	DecisionType string
}

// DecodeUser extracts the message text ("" when absent).
func (m *Mapper) DecodeUser(p map[string]string) UserMessage {
	return UserMessage{Text: p["text"]}
}

// DecodeDelta extracts the assistant text.
func (m *Mapper) DecodeDelta(p map[string]string) AssistantDelta {
	return AssistantDelta{Text: p["text"]}
}

// DecodeTool extracts the tool call fields.
func (m *Mapper) DecodeTool(p map[string]string) ToolCall {
	return ToolCall{
		ID:      firstNonEmpty(p["id"], p["tool_use_id"]),
		Name:    p["name"],
		Input:   p["input"],
		Content: p["content"],
		IsError: p["is_error"] == "true",
	}
}

// DecodeError extracts the run.failed fields.
func (m *Mapper) DecodeError(p map[string]string) RunError {
	return RunError{Message: p["message"], Category: p["category"]}
}

// DecodeIdle extracts the stop reason.
func (m *Mapper) DecodeIdle(p map[string]string) Idle {
	return Idle{StopReason: p["stop_reason"]}
}

// DecodeUsage extracts the four-bucket token counts. Absent/invalid
// numbers default to 0 (interrupted streams must not crash the parse).
func (m *Mapper) DecodeUsage(p map[string]string) Usage {
	return Usage{
		InputTokens:     atoiSafe(p["input_tokens"]),
		OutputTokens:    atoiSafe(p["output_tokens"]),
		CacheReadTokens: atoiSafe(p["cache_read_tokens"]),
		CacheWriteTokens: atoiSafe(p["cache_write_tokens"]),
	}
}

// DecodeRequiresAction extracts the pending approval fields.
func (m *Mapper) DecodeRequiresAction(p map[string]string) RequiresAction {
	return RequiresAction{
		PendingID:    p["pending_id"],
		ToolUseID:    p["tool_use_id"],
		DecisionType: p["decision_type"],
	}
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}

func atoiSafe(s string) int64 {
	if s == "" {
		return 0
	}
	n, err := strconv.ParseInt(s, 10, 64)
	if err != nil {
		return 0
	}
	return n
}

// JSONData decodes a JSON-encoded string payload value into out (used for
// structured fields like citation_bundle or tool input when needed).
func JSONData(raw string, out any) error {
	if raw == "" {
		return nil
	}
	dec := json.NewDecoder(strings.NewReader(raw))
	return dec.Decode(out)
}
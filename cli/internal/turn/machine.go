// Package turn implements the message_id state machine for a single run
// (design.md §5.3). Only events whose payload.message_id matches the target
// turn change run state; a stale idle from an earlier turn must never end
// the run.
package turn

import (
	"sync"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
)

// Status is the terminal status of a run (subset used by the machine;
// timeout/signal/action_required are applied by the runner).
type Status string

const (
	StatusRunning   Status = "running"
	StatusCompleted Status = "completed"
	StatusError     Status = "error"
	// StatusActionRequired is set when the target turn parks on an approval
	// the headless CLI cannot answer.
	StatusActionRequired Status = "action_required"
)

// Machine tracks the state of one turn execution.
type Machine struct {
	mu sync.Mutex

	// MessageID is the turn anchor, learned from the first message.user
	// event after baseline. Empty until anchored.
	MessageID string

	// Anchored reports whether the target message_id is known.
	Anchored bool

	// Status is the current run status.
	Status Status

	// Error accumulates the run.failed message (non-empty when observed).
	Error string

	// LastStopReason from the target turn's session.idle.
	LastStopReason string

	// Usage accumulates the target turn's four-bucket token counts.
	Usage Usage

	// ObservedError tracks whether any run.failed was seen for the target
	// turn, so the runner can still wait for usage/idle before finishing.
	ObservedError bool
}

// Usage is the four-bucket token accounting (design.md §3.3).
type Usage struct {
	InputTokens      int64
	OutputTokens     int64
	CacheReadTokens  int64
	CacheWriteTokens int64
}

// NewMachine returns an unanchored machine.
func NewMachine() *Machine {
	return &Machine{Status: StatusRunning}
}

// Anchor sets the turn's message_id from the first message.user event.
// Returns false when an anchor is already set (second user message in the
// same stream is ignored — one run, one turn).
func (m *Machine) Anchor(id string) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.Anchored {
		return false
	}
	m.MessageID = id
	m.Anchored = true
	return true
}

// Matches reports whether a frame belongs to the target turn. Frames
// before anchoring (with a different/no message_id) are buffered by the
// runner; once anchored, only matching frames mutate state.
func (m *Machine) Matches(frame *backend.SSEFrame) bool {
	if !m.Anchored {
		return false
	}
	return frame.Payload["message_id"] == m.MessageID
}

// NoteError records a run.failed for the target turn (does not finish).
func (m *Machine) NoteError(message string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.ObservedError = true
	if m.Error == "" {
		m.Error = message
	}
}

// NoteIdle records the target turn's idle and completes the run. The
// runner decides error vs completed from ObservedError + stop_reason.
func (m *Machine) NoteIdle(stopReason string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.LastStopReason = stopReason
	m.Status = StatusCompleted
}

// NoteUsage accumulates the four-bucket usage for the target turn.
func (m *Machine) NoteUsage(input, output, cacheRead, cacheWrite int64) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.Usage.InputTokens = input
	m.Usage.OutputTokens = output
	m.Usage.CacheReadTokens = cacheRead
	m.Usage.CacheWriteTokens = cacheWrite
}

// RequiresAction parks the run on an approval (headless cannot answer).
func (m *Machine) RequiresAction() {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.Status = StatusActionRequired
}

// Snapshot returns a copy of the current state for the runner.
func (m *Machine) Snapshot() Snapshot {
	m.mu.Lock()
	defer m.mu.Unlock()
	return Snapshot{
		MessageID:      m.MessageID,
		Anchored:       m.Anchored,
		Status:         m.Status,
		Error:          m.Error,
		LastStopReason: m.LastStopReason,
		Usage:          m.Usage,
		ObservedError:  m.ObservedError,
	}
}

// Snapshot is an immutable view of the machine state.
type Snapshot struct {
	MessageID      string
	Anchored       bool
	Status         Status
	Error          string
	LastStopReason string
	Usage          Usage
	ObservedError  bool
}

// Finished reports whether the run reached a terminal state.
func (s Snapshot) Finished() bool {
	return s.Status != StatusRunning
}

// ErrorStatus derives the final status: action_required wins over
// error/complete; observed error or an execution_error stop reason yields
// error, else completed.
func (s Snapshot) ErrorStatus() Status {
	if s.Status == StatusActionRequired {
		return StatusActionRequired
	}
	if s.ObservedError || s.LastStopReason == "execution_error" {
		return StatusError
	}
	return StatusCompleted
}

// Stale returns true when the frame belongs to a different turn entirely
// (idle for an old message after the current one anchored).
func (s Snapshot) Stale(frame *backend.SSEFrame) bool {
	mid := frame.Payload["message_id"]
	if mid == "" {
		return false
	}
	return s.Anchored && mid != s.MessageID
}
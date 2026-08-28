package turn

import (
	"bufio"
	"encoding/json"
	"os"
	"testing"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
)

// loadFrames reads a fixture file (one flat SessionEventFrame JSON per
// line) into frames.
func loadFrames(t *testing.T, path string) []*backend.SSEFrame {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Fatalf("open fixture: %v", err)
	}
	defer f.Close()

	var frames []*backend.SSEFrame
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		if line == "" {
			continue
		}
		var fr backend.SSEFrame
		if err := json.Unmarshal([]byte(line), &fr); err != nil {
			t.Fatalf("parse fixture line %q: %v", line, err)
		}
		frames = append(frames, &fr)
	}
	if err := sc.Err(); err != nil {
		t.Fatalf("scan fixture: %v", err)
	}
	return frames
}

// drive replays frames through the machine exactly like the runner does.
func drive(m *Machine, frames []*backend.SSEFrame) {
	for _, f := range frames {
		if f.IsHeartbeat() {
			continue
		}
		et := *f.EventType
		if et == "message.user" && !m.Snapshot().Anchored {
			m.Anchor(f.Payload["message_id"])
			continue
		}
		if !m.Matches(f) {
			continue
		}
		switch et {
		case "run.failed":
			m.NoteError(f.Payload["message"])
		case "runtime.engine.usage":
			m.NoteUsage(0, 0, 0, 0)
		case "session.idle":
			m.NoteIdle(f.Payload["stop_reason"])
		case "session.requires_action":
			m.RequiresAction()
		}
	}
}

func TestSuccess(t *testing.T) {
	m := NewMachine()
	drive(m, loadFrames(t, "../../testdata/sse/success.jsonl"))
	s := m.Snapshot()
	if !s.Anchored || s.MessageID != "msg-0001" {
		t.Fatalf("anchor = %+v", s)
	}
	if !s.Finished() || s.ErrorStatus() != StatusCompleted {
		t.Fatalf("status = %+v", s)
	}
	if s.LastStopReason != "completed" {
		t.Fatalf("stop reason = %q", s.LastStopReason)
	}
}

func TestErrorOrder(t *testing.T) {
	m := NewMachine()
	drive(m, loadFrames(t, "../../testdata/sse/error.jsonl"))
	s := m.Snapshot()
	if !s.Finished() || s.ErrorStatus() != StatusError {
		t.Fatalf("status = %+v", s)
	}
	if !s.ObservedError || s.Error == "" {
		t.Fatalf("error not recorded: %+v", s)
	}
}

func TestInterrupt(t *testing.T) {
	m := NewMachine()
	drive(m, loadFrames(t, "../../testdata/sse/interrupt.jsonl"))
	s := m.Snapshot()
	if !s.Finished() {
		t.Fatalf("not finished: %+v", s)
	}
	// stop_reason=user_interrupt with no observed error → completed
	// (the runner maps the interrupt origin to status).
	if s.ErrorStatus() != StatusCompleted {
		t.Fatalf("status = %+v", s)
	}
	if s.LastStopReason != "user_interrupt" {
		t.Fatalf("stop reason = %q", s.LastStopReason)
	}
}

func TestRequiresAction(t *testing.T) {
	m := NewMachine()
	drive(m, loadFrames(t, "../../testdata/sse/requires-action.jsonl"))
	s := m.Snapshot()
	if s.Status != StatusActionRequired {
		t.Fatalf("status = %+v", s)
	}
}

func TestStaleIdleDoesNotEndRun(t *testing.T) {
	m := NewMachine()
	frames := loadFrames(t, "../../testdata/sse/stale-idle.jsonl")

	// Frames 0-1: user anchor + matching delta.
	drive(m, frames[:2])
	// Frame 2: stale idle for msg-0006 — must NOT end the run.
	drive(m, frames[2:3])
	if s := m.Snapshot(); s.Finished() {
		t.Fatalf("stale idle ended the run early: %+v", s)
	}
	// Frames 3-4: usage + target idle — run ends now.
	drive(m, frames[3:])
	s := m.Snapshot()
	if !s.Finished() || s.ErrorStatus() != StatusCompleted {
		t.Fatalf("status = %+v", s)
	}
	if s.MessageID != "msg-0007" {
		t.Fatalf("anchored wrong turn: %+v", s)
	}
}

func TestHeartbeatIgnored(t *testing.T) {
	m := NewMachine()
	drive(m, loadFrames(t, "../../testdata/sse/heartbeat.jsonl"))
	s := m.Snapshot()
	if !s.Finished() || s.ErrorStatus() != StatusCompleted {
		t.Fatalf("status = %+v", s)
	}
}
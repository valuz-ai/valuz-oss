package output

import (
	"bytes"
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"
)

func sampleResult() RunResult {
	start := time.Date(2026, 8, 27, 10, 0, 0, 0, time.UTC)
	return RunResult{
		SchemaVersion: "valuz.run-result/v1",
		RunID:         "run-1",
		SessionID:     "sess-1",
		ProjectID:     "proj-1",
		MessageID:     "msg-1",
		AgentSlug:     "valurion",
		Runtime:       "deepagents",
		Model:         "claude-sonnet-4-6",
		Status:        StatusCompleted,
		ExitCode:      0,
		StartedAt:     start.Format(time.RFC3339Nano),
		FinishedAt:    start.Add(90 * time.Second).Format(time.RFC3339Nano),
		DurationMS:    90000,
		Usage:         Usage{InputTokens: 1000, OutputTokens: 200, CacheReadTokens: 0, CacheWriteTokens: 0},
		NumTurns:      1,
		FinalMessage:  "done",
	}
}

func TestRunResultMarshal(t *testing.T) {
	raw, err := sampleResult().Marshal()
	if err != nil {
		t.Fatalf("Marshal: %v", err)
	}
	var decoded map[string]any
	if err := json.Unmarshal(raw, &decoded); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	for _, field := range []string{"schema_version", "run_id", "session_id", "project_id", "message_id", "agent_slug", "runtime", "model", "status", "exit_code", "started_at", "finished_at", "duration_ms", "usage", "num_turns", "final_message"} {
		if _, ok := decoded[field]; !ok {
			t.Fatalf("missing field %q", field)
		}
	}
	usage, _ := decoded["usage"].(map[string]any)
	for _, u := range []string{"input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"} {
		if _, ok := usage[u]; !ok {
			t.Fatalf("usage missing %q", u)
		}
	}
}

func TestExitCodeFor(t *testing.T) {
	cases := map[string]int{
		StatusCompleted: 0, StatusError: 3, StatusTimeout: 2,
		StatusActionRequired: 7, StatusAuthError: 6, StatusInternalError: 5,
	}
	for status, want := range cases {
		if got := ExitCodeFor(status); got != want {
			t.Fatalf("ExitCodeFor(%s) = %d, want %d", status, got, want)
		}
	}
}

func TestSinkJSONLExactlyOneRunEnd(t *testing.T) {
	var buf bytes.Buffer
	sink, err := NewSink("jsonl", &buf, "")
	if err != nil {
		t.Fatalf("NewSink: %v", err)
	}

	ev := RunEvent{SchemaVersion: "valuz.run-event/v1", RunID: "run-1", SessionID: "sess-1", Source: SourceLive, Type: "message.assistant.delta", Data: map[string]any{"text": "hi"}}
	if err := sink.Event(ev); err != nil {
		t.Fatalf("Event: %v", err)
	}
	if _, err := sink.End(sampleResult()); err != nil {
		t.Fatalf("End: %v", err)
	}
	// Second End must be rejected (exactly-once).
	if _, err := sink.End(sampleResult()); err == nil {
		t.Fatal("second End should fail")
	}

	lines := strings.Split(strings.TrimRight(buf.String(), "\n"), "\n")
	if len(lines) != 2 {
		t.Fatalf("expected 2 lines, got %d:\n%s", len(lines), buf.String())
	}
	var last map[string]any
	if err := json.Unmarshal([]byte(lines[1]), &last); err != nil {
		t.Fatalf("last line not JSON: %v", err)
	}
	if last["type"] != RunEndType {
		t.Fatalf("last event type = %v", last["type"])
	}
	if _, ok := last["data"].(map[string]any)["result"]; !ok {
		t.Fatal("run.end missing result")
	}
}

func TestSinkJSONDocument(t *testing.T) {
	var buf bytes.Buffer
	sink, err := NewSink("json", &buf, "")
	if err != nil {
		t.Fatalf("NewSink: %v", err)
	}
	code, err := sink.End(sampleResult())
	if err != nil || code != 0 {
		t.Fatalf("End: code=%d err=%v", code, err)
	}
	var doc map[string]any
	if err := json.Unmarshal(buf.Bytes(), &doc); err != nil {
		t.Fatalf("not JSON: %v", err)
	}
	if doc["schema_version"] != "valuz.run-result/v1" {
		t.Fatalf("schema_version = %v", doc["schema_version"])
	}
}

func TestSinkTrajectoryMirror(t *testing.T) {
	dir := t.TempDir()
	path := dir + "/traj.jsonl"

	var buf bytes.Buffer
	sink, err := NewSink("jsonl", &buf, path)
	if err != nil {
		t.Fatalf("NewSink: %v", err)
	}
	if err := sink.Event(RunEvent{SchemaVersion: "valuz.run-event/v1", RunID: "r", Source: SourceLive, Type: "session.todos.update", Data: map[string]any{"todos": "[]"}}); err != nil {
		t.Fatalf("Event: %v", err)
	}
	if _, err := sink.End(sampleResult()); err != nil {
		t.Fatalf("End: %v", err)
	}
	if err := sink.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	fileData, err := readFile(path)
	if err != nil {
		t.Fatalf("read trajectory: %v", err)
	}
	if fileData != buf.String() {
		t.Fatal("trajectory file does not mirror stdout")
	}
}

func TestRedactPolicy(t *testing.T) {
	p := DefaultRedactPolicy()

	// secret-like masking
	if got := p.RedactField("Bearer eyJhbGciOiJIUzI1NiJ9.abc"); strings.Contains(got, "eyJ") {
		t.Fatalf("secret survived: %q", got)
	}

	// truncation with marker
	big := strings.Repeat("x", p.MaxFieldBytes+10)
	got := p.RedactField(big)
	if !strings.HasSuffix(got, "[truncated=true]") {
		t.Fatalf("no truncation marker: %q", got[:40])
	}

	// oversized data map collapses to a marker object: several fields
	// each capped at MaxFieldBytes still exceed MaxDataBytes together.
	data := map[string]any{
		"a": strings.Repeat("y", p.MaxFieldBytes),
		"b": strings.Repeat("z", p.MaxFieldBytes),
		"c": strings.Repeat("w", p.MaxFieldBytes),
		"d": strings.Repeat("v", p.MaxFieldBytes),
		"e": strings.Repeat("u", p.MaxFieldBytes),
	}
	out := p.RedactData(data)
	if out["truncated"] != true {
		t.Fatalf("data not truncated: %v", out)
	}
}

func readFile(path string) (string, error) {
	data, err := os.ReadFile(path)
	return string(data), err
}

// keep os import minimal for tests

// TestRunEndGolden asserts the exact run.end line shape (design §6.2),
// catching field renames or ordering drift the "fields exist" assertion
// would miss.
func TestRunEndGolden(t *testing.T) {
	var buf bytes.Buffer
	sink, err := NewSink("jsonl", &buf, "")
	if err != nil {
		t.Fatalf("NewSink: %v", err)
	}
	res := sampleResult()
	res.RunID = "run-golden"
	res.MessageID = "msg-golden"
	res.Status = StatusCompleted
	res.ExitCode = 0
	if _, err := sink.End(res); err != nil {
		t.Fatalf("End: %v", err)
	}
	want := `{"schema_version":"valuz.run-event/v1","run_id":"run-golden","session_id":"sess-1","project_id":"proj-1","message_id":"msg-golden","source":"cli","source_seq":null,"type":"run.end","data":{"result":{"schema_version":"valuz.run-result/v1","run_id":"run-golden","session_id":"sess-1","project_id":"proj-1","message_id":"msg-golden","agent_slug":"valurion","runtime":"deepagents","model":"claude-sonnet-4-6","status":"completed","exit_code":0,"started_at":"2026-08-27T10:00:00Z","finished_at":"2026-08-27T10:01:30Z","duration_ms":90000,"usage":{"input_tokens":1000,"output_tokens":200,"cache_read_tokens":0,"cache_write_tokens":0},"num_turns":1,"final_message":"done"}}}`
	got := strings.TrimRight(buf.String(), "\n")
	if got != want {
		t.Fatalf("run.end golden mismatch:\n got: %s\nwant: %s", got, want)
	}
}

// Package runner orchestrates a single connect-only run (design.md §5.3):
// readiness → project → session → pre-subscribe SSE → POST message →
// consume events against the turn machine → aggregate the result.
package runner

import (
	"context"
	"fmt"
	"io"
	"time"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/event"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/output"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/project"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/turn"
)

// Default values (overridable via Options).
const (
	defaultReconnectMax   = 3
	defaultStreamIdle     = 30 * time.Second // backend heartbeats every 15s
	postGraceAfterError   = 30 * time.Second // wait for a turn anchor after a lost POST
	streamDrainGrace      = 2 * time.Second
	defaultPermissionMode = "default"
)

// Options configures one run.
type Options struct {
	ProjectID      string // resolved project id (mutually exclusive with Cwd)
	Cwd            string // directory to resolve to a project
	AgentSlug      string // optional explicit agent binding
	ModelID        string
	ProviderID     string
	RuntimeID      string
	PermissionMode string // default "default"; eval sandboxes pass "full_access"
	Prompt         string
	Timeout        time.Duration // 0 = no wall-clock limit
	RunID          string
	// EventSink receives live RunEvents (jsonl protocol); nil = none.
	EventSink *output.Sink
	// Signal, when non-empty, records the OS signal that cancelled the
	// run (e.g. "SIGINT" → exit 130, "SIGTERM" → exit 143).
	Signal string
	// ReconnectMaxAttempts caps SSE reconnects (0 = default 3).
	ReconnectMaxAttempts int
	// HumanOutput enables plain-text assistant deltas on stdout (only
	// for --output human; machine protocols must never mix).
	HumanOutput bool
	// MCPSlugs enables MCP data sources for the session (reportify etc).
	MCPSlugs []string
	// SkillIDs attaches extra skills to the session after creation.
	SkillIDs []string
}

// Result is the aggregated outcome of a run (consumed by the output layer).
type Result struct {
	RunID     string
	SessionID string
	ProjectID string
	MessageID string
	AgentSlug string
	Runtime   string
	Model     string
	Status    string
	Usage     turn.Usage
	FinalText string
	Error     string
	NumTurns  int
	Signal    string
	StartedAt time.Time
	Finished  time.Time
}

// Runner executes runs against a backend.
type Runner struct {
	Control *backend.ControlClient
	Stream  *backend.StreamClient
	Mapper  *event.Mapper
	// Stdout receives human progress when non-nil (human mode only).
	Stdout io.Writer
	// human gates Stdout: machine protocols must never mix plain text.
	human bool
}

// New builds a runner.
func New(control *backend.ControlClient, stream *backend.StreamClient, stdout io.Writer) *Runner {
	return &Runner{
		Control: control,
		Stream:  stream,
		Mapper:  event.New(),
		Stdout:  stdout,
	}
}

// QuickChatProjectID is the singleton chat project minted for quick
// chats (projects/service.go ensure_chat_project; GET /v1/projects/
// chat-default auto-seeds it).
const QuickChatProjectID = "chat-default"

// Run executes one turn and returns the aggregated result. Status
// derivation follows design.md §5.4. The product execution shapes map to
// Options as follows: quick chat = neither ProjectID nor Cwd set (uses
// chat-default); project chat = ProjectID or Cwd set; task orchestration
// lives in the separate task command group.
func (r *Runner) Run(ctx context.Context, o Options) (*Result, error) {
	if o.ProjectID == "" && o.Cwd == "" {
		o.ProjectID = QuickChatProjectID // quick chat: auto chat project
	}
	if o.Prompt == "" {
		return nil, errs.New(errs.KindUsage, "prompt is required")
	}
	r.human = o.HumanOutput

	started := time.Now()
	runCtx := ctx
	var cancel context.CancelFunc
	if o.Timeout > 0 {
		runCtx, cancel = context.WithTimeout(ctx, o.Timeout)
		defer cancel()
	}

	// 1. readiness
	if err := r.readiness(runCtx); err != nil {
		return nil, err
	}

	// 2. project
	projectID := o.ProjectID
	if projectID == "" {
		pid, err := r.resolveProject(runCtx, o.Cwd)
		if err != nil {
			return nil, err
		}
		projectID = pid
	}

	// 3. create session
	session, err := r.createSession(runCtx, projectID, o)
	if err != nil {
		return nil, err
	}

	// 4. pre-subscribe SSE (before POST so no live delta is lost)
	streamCtx, streamCancel := context.WithCancel(runCtx)
	defer streamCancel()

	machine := turn.NewMachine()
	seen := map[string]bool{}
	eventErr := make(chan error, 1)
	r.Stream.ReconnectMaxAttempts = o.ReconnectMaxAttempts
	if r.Stream.ReconnectMaxAttempts <= 0 {
		r.Stream.ReconnectMaxAttempts = defaultReconnectMax
	}
	r.Stream.ReconnectBaseDelay = time.Second
	r.Stream.IdleDeadline = defaultStreamIdle
	go func() {
		eventErr <- r.Stream.Stream(streamCtx, "/v1/sessions/"+session.ID+"/events/stream", 0, func(ctx context.Context, f *backend.SSEFrame) error {
			return r.handleFrame(machine, seen, f, o.RunID, session.ID, projectID, o.EventSink)
		})
	}()

	// 5. POST message (never retried; design.md §5.3)
	postCtx, postCancel := context.WithTimeout(runCtx, 30*time.Second)
	var postErr error
	var msgResp backend.SessionDetail
	if err := r.Control.Post(postCtx, "/v1/sessions/"+session.ID+"/messages",
		backend.SessionMessageRequest{Prompt: o.Prompt}, &msgResp); err != nil {
		postErr = err
		postCancel()
		// Response lost is not an abort: the turn may already be running
		// on the stream. Wait (bounded) for an anchor/terminal; only fail
		// if the backend was genuinely unreachable or nothing arrives.
		if !machine.Snapshot().Anchored {
			select {
			case <-machine.Done():
			case <-time.After(postGraceAfterError):
				streamCancel()
				<-eventErr
				return nil, postErr
			case <-runCtx.Done():
				streamCancel()
				<-eventErr
				return nil, postErr
			}
		}
	} else {
		postCancel()
	}

	// 6. wait for terminal: machine done (target idle / action required),
	// stream failure, or run context end.
	var streamErr error
	select {
	case <-machine.Done():
		streamCancel()
		// Drain the stream goroutine so its writes finish before we read
		// machine state (bounded by the drain grace).
		select {
		case streamErr = <-eventErr:
		case <-time.After(streamDrainGrace):
		}
	case streamErr = <-eventErr:
		// Stream ended (or failed) before the machine finished — if the
		// run is actually terminal we proceed; otherwise reconcile.
	case <-runCtx.Done():
		streamCancel()
		select {
		case streamErr = <-eventErr:
		case <-time.After(streamDrainGrace):
		}
	}

	// 7. classify the outcome (design §5.4 priority order).
	return r.classify(runCtx, machine, session.ID, projectID, o, started, streamErr, streamCancel)
}

// classify derives the terminal status and assembles the Result, emitting
// the run.end document on the sink in every started-run path.
func (r *Runner) classify(runCtx context.Context, m *turn.Machine, sessionID, projectID string, o Options, started time.Time, streamErr error, streamCancel context.CancelFunc) (*Result, error) {
	snap := m.Snapshot()
	finished := time.Now()
	res := &Result{
		RunID:     o.RunID,
		SessionID: sessionID,
		ProjectID: projectID,
		MessageID: snap.MessageID,
		AgentSlug: o.AgentSlug,
		Runtime:   "",
		Model:     o.ModelID,
		Usage:     snap.Usage,
		FinalText: snap.FinalText,
		NumTurns:  1, // one run, one new session, one turn (Slice 2 scope)
		Signal:    o.Signal,
		StartedAt: started,
		Finished:  finished,
	}

	switch {
	case snap.Finished() && snap.Status == turn.StatusActionRequired:
		// Headless cannot answer an approval: interrupt best-effort and
		// report action_required (design §4.2 rule 6 fail-fast).
		r.interruptBestEffort(runCtx, sessionID)
		res.Status = output.StatusActionRequired
	case snap.Finished():
		// Target turn reached a terminal idle.
		if snap.ErrorStatus() == turn.StatusError {
			res.Status = output.StatusError
			res.Error = snap.Error
		} else {
			res.Status = output.StatusCompleted
		}
	case runCtx.Err() == context.Canceled:
		// Context cancelled by a signal (or parent ctx): classify as
		// interrupted. The concrete signal name is filled in by the
		// command layer from its signal channel (runner can't observe
		// which signal fired).
		r.interruptBestEffort(runCtx, sessionID)
		res.Status = output.StatusInterrupted
	case runCtx.Err() == context.DeadlineExceeded:
		r.interruptBestEffort(runCtx, sessionID)
		res.Status = output.StatusTimeout
	case streamErr != nil:
		// Reconnect exhausted (or stream failed): reconcile against
		// durable history before giving up (design.md §5.3 step 8).
		if r.reconcile(runCtx, m, sessionID, projectID, o) {
			snap = m.Snapshot()
			res.MessageID = snap.MessageID
			res.Usage = snap.Usage
			res.FinalText = snap.FinalText
			if snap.ErrorStatus() == turn.StatusError {
				res.Status = output.StatusError
				res.Error = snap.Error
			} else {
				res.Status = output.StatusCompleted
			}
			break
		}
		streamCancel()
		// Emit a run.end with an internal error so the JSONL consumer
		// still gets its exactly-once terminal line.
		res.Status = output.StatusInternalError
		res.Error = streamErr.Error()
		r.emitEnd(o.EventSink, res)
		return nil, errs.Wrap(errs.KindInternal, streamErr, "stream ended before the turn completed (session %s)", sessionID)
	default:
		// Context ended without a signal/timeout and no stream error —
		// treat as internal (should not happen).
		res.Status = output.StatusInternalError
		res.Error = "run ended without a terminal event"
	}

	r.emitEnd(o.EventSink, res)
	return res, nil
}

// emitEnd writes the run.end document exactly once per started run.
func (r *Runner) emitEnd(sink *output.Sink, res *Result) {
	if sink == nil {
		return
	}
	doc := RunResultFrom(res)
	if _, err := sink.End(doc); err != nil {
		// End is best-effort on the terminal path; the JSONL stream
		// already carries the terminal event.
		return
	}
}

// RunResultFrom converts a runner Result into the output document.
func RunResultFrom(res *Result) output.RunResult {
	return output.RunResult{
		SchemaVersion: "valuz.run-result/v1",
		RunID:         res.RunID,
		SessionID:     res.SessionID,
		ProjectID:     res.ProjectID,
		MessageID:     res.MessageID,
		AgentSlug:     res.AgentSlug,
		Runtime:       res.Runtime,
		Model:         res.Model,
		Status:        res.Status,
		ExitCode:      output.ExitCodeFor(res.Status),
		StartedAt:     res.StartedAt.UTC().Format(time.RFC3339Nano),
		FinishedAt:    res.Finished.UTC().Format(time.RFC3339Nano),
		DurationMS:    res.Finished.Sub(res.StartedAt).Milliseconds(),
		Usage: output.Usage{
			InputTokens:      res.Usage.InputTokens,
			OutputTokens:     res.Usage.OutputTokens,
			CacheReadTokens:  res.Usage.CacheReadTokens,
			CacheWriteTokens: res.Usage.CacheWriteTokens,
		},
		NumTurns:     res.NumTurns,
		FinalMessage: res.FinalText,
		Error:        res.Error,
	}
}

func (r *Runner) readiness(ctx context.Context) error {
	probeCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	var st backend.SystemStatus
	if err := r.Control.Get(probeCtx, "/v1/system/status", &st); err != nil {
		return err
	}
	return nil
}

func (r *Runner) resolveProject(ctx context.Context, cwd string) (string, error) {
	pid, err := (&project.Resolver{Client: r.Control}).Resolve(ctx, cwd)
	if err != nil {
		return "", err
	}
	return pid, nil
}

func (r *Runner) createSession(ctx context.Context, projectID string, o Options) (*backend.SessionDetail, error) {
	perm := o.PermissionMode
	if perm == "" {
		perm = defaultPermissionMode
	}
	body := backend.SessionCreateRequest{ProjectID: projectID, PermissionMode: &perm, MCPSlugs: o.MCPSlugs}
	if o.ModelID != "" {
		body.ModelID = &o.ModelID
	}
	if o.ProviderID != "" {
		body.ProviderID = &o.ProviderID
	}
	if o.RuntimeID != "" {
		body.RuntimeID = &o.RuntimeID
	}
	if o.AgentSlug != "" {
		body.AgentSlug = &o.AgentSlug
	}

	createCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	var session backend.SessionDetail
	if err := r.Control.Post(createCtx, "/v1/sessions", body, &session); err != nil {
		return nil, err
	}
	if session.ID == "" {
		return nil, errs.New(errs.KindInternal, "backend returned a session without id")
	}
	if len(o.SkillIDs) > 0 {
		if err := r.attachSkills(ctx, session.ID, o.SkillIDs); err != nil {
			return nil, err
		}
	}
	return &session, nil
}

// attachSkills replaces the session's extra skill list (PUT
// /v1/sessions/{id}/skills).
func (r *Runner) attachSkills(ctx context.Context, sessionID string, skillIDs []string) error {
	skCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	var resp backend.SessionSkillsResponse
	if err := r.Control.Put(skCtx, "/v1/sessions/"+sessionID+"/skills",
		backend.SessionSkillsRequest{SkillIDs: skillIDs}, &resp); err != nil {
		return err
	}
	return nil
}

// handleFrame drives the turn machine. Unmatched/stale frames are ignored;
// duplicate uids (reconnect replay) are dropped; live events are
// forwarded to the sink when one is configured.
func (r *Runner) handleFrame(m *turn.Machine, seen map[string]bool, f *backend.SSEFrame, runID, session, project string, sink *output.Sink) error {
	if f.IsHeartbeat() {
		return nil
	}
	if f.EventUID != nil && *f.EventUID != "" {
		if seen[*f.EventUID] {
			return nil // already processed on a previous connection
		}
		seen[*f.EventUID] = true
	}
	et := *f.EventType

	// Anchor on the first message.user of this stream.
	if et == event.EventUser && !m.Snapshot().Anchored {
		m.Anchor(f.Payload["message_id"])
	}
	if !m.Snapshot().Anchored {
		return nil
	}

	snap := m.Snapshot()
	emit := func(evType string, data map[string]any) error {
		if sink == nil {
			return nil
		}
		mid := f.Payload["message_id"]
		ev := output.RunEvent{
			SchemaVersion: "valuz.run-event/v1",
			RunID:         runID,
			SessionID:     session,
			ProjectID:     project,
			MessageID:     mid,
			EventUID:      strOrNil(f.EventUID),
			Source:        output.SourceLive,
			SourceSeq:     int64OrNil(f.Seq),
			Type:          evType,
			Data:          data,
		}
		return sink.Event(ev)
	}

	// Stale frames (different turn) never change state or emit.
	if snap.Anchored && !m.Matches(f) {
		return nil
	}

	switch et {
	case event.EventAssistantDelta, event.EventAssistantText:
		d := r.Mapper.DecodeDelta(f.Payload)
		m.AppendText(d.Text)
		if r.human && r.Stdout != nil && d.Text != "" {
			// Human mode only: the machine protocol streams must never
			// mix plain text with JSONL/JSON (design §5.2).
			fmt.Fprint(r.Stdout, output.NormalizeNewlines(errs.Redact(d.Text)))
		}
		if err := emit(et, map[string]any{"text": d.Text}); err != nil {
			return err
		}
	case event.EventRunFailed:
		e := r.Mapper.DecodeError(f.Payload)
		m.NoteError(e.Message)
		if err := emit(et, map[string]any{"message": e.Message, "category": e.Category}); err != nil {
			return err
		}
	case event.EventUsage:
		u := r.Mapper.DecodeUsage(f.Payload)
		m.NoteUsage(u.InputTokens, u.OutputTokens, u.CacheReadTokens, u.CacheWriteTokens)
		if err := emit(et, map[string]any{
			"input_tokens": u.InputTokens, "output_tokens": u.OutputTokens,
			"cache_read_tokens": u.CacheReadTokens, "cache_write_tokens": u.CacheWriteTokens,
		}); err != nil {
			return err
		}
	case event.EventIdle:
		idle := r.Mapper.DecodeIdle(f.Payload)
		m.NoteIdle(idle.StopReason)
		if err := emit(et, map[string]any{"stop_reason": idle.StopReason}); err != nil {
			return err
		}
	case event.EventRequiresAction:
		m.RequiresAction()
		ra := r.Mapper.DecodeRequiresAction(f.Payload)
		if err := emit(et, map[string]any{"pending_id": ra.PendingID}); err != nil {
			return err
		}
	}
	return nil
}

// interruptBestEffort asks the backend to stop the current turn; failures
// are ignored (the run is ending anyway).
func (r *Runner) interruptBestEffort(ctx context.Context, sessionID string) {
	if sessionID == "" {
		return
	}
	ictx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	var detail backend.SessionDetail
	_ = r.Control.Post(ictx, "/v1/sessions/"+sessionID+"/interrupt", nil, &detail)
}

// reconcile replays durable history after reconnects are exhausted and
// looks for the target turn's terminal idle. Returns true when the run
// can be completed from history.
func (r *Runner) reconcile(ctx context.Context, m *turn.Machine, sessionID, projectID string, o Options) bool {
	// The REST history shape differs from the flat SSE frame; decode
	// into envelopes.
	var history struct {
		SessionID string `json:"session_id"`
		Items     []struct {
			Seq   int64 `json:"seq"`
			Event struct {
				EventType string            `json:"event_type"`
				Payload   map[string]string `json:"payload"`
			} `json:"event"`
			Timestamp *int64  `json:"timestamp"`
			EventUID  *string `json:"event_uid"`
		} `json:"items"`
	}
	rc, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	if err := r.Control.Get(rc, "/v1/sessions/"+sessionID+"/events", &history); err != nil {
		return false
	}

	// Feed only frames belonging to the anchored turn; the machine
	// ignores everything else.
	seen := map[string]bool{}
	for _, it := range history.Items {
		f := &backend.SSEFrame{
			Seq:       int(it.Seq),
			EventType: &it.Event.EventType,
			Payload:   it.Event.Payload,
			Timestamp: it.Timestamp,
			EventUID:  it.EventUID,
		}
		if err := r.handleFrame(m, seen, f, o.RunID, sessionID, projectID, nil); err != nil {
			return false
		}
	}
	return m.Snapshot().Finished()
}

func strOrNil(v *string) string {
	if v == nil {
		return ""
	}
	return *v
}

func int64OrNil(v int) *int64 {
	if v == 0 {
		return nil
	}
	vv := int64(v)
	return &vv
}

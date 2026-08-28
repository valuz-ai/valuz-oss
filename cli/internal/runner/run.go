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
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/event"
	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/project"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/turn"
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
	StartedAt time.Time
	Finished  time.Time
}

// Runner executes runs against a backend.
type Runner struct {
	Control *backend.ControlClient
	Stream  *backend.StreamClient
	Mapper  *event.Mapper
	// Stdout receives human progress when non-nil.
	Stdout io.Writer
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

// Run executes one turn and returns the aggregated result. The status
// derivation follows design.md §5.4 (subset for Slice 2; timeout/signal/
// action_required hooks land in Slice 4).
func (r *Runner) Run(ctx context.Context, o Options) (*Result, error) {
	if o.ProjectID == "" && o.Cwd == "" {
		return nil, errs.New(errs.KindUsage, "either --project or --cwd is required")
	}
	if o.Prompt == "" {
		return nil, errs.New(errs.KindUsage, "prompt is required")
	}

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
	eventErr := make(chan error, 1)
	go func() {
		eventErr <- r.Stream.Stream(streamCtx, session.ID, 0, func(ctx context.Context, f *backend.SSEFrame) error {
			return r.handleFrame(machine, f, o.RunID, session.ID, projectID)
		})
	}()

	// 5. POST message (never retried; see design.md §5.3)
	postCtx, postCancel := context.WithTimeout(runCtx, 30*time.Second)
	defer postCancel()
	var msgResp backend.SessionDetail
	if err := r.Control.Post(postCtx, "/v1/sessions/"+session.ID+"/messages",
		backend.SessionMessageRequest{Prompt: o.Prompt}, &msgResp); err != nil {
		streamCancel()
		<-eventErr
		return nil, err
	}

	// 6. wait for the turn machine to finish or ctx to end
	var streamErr error
	select {
	case streamErr = <-eventErr:
		// stream closed by server or error before terminal — probe once
	case <-runCtx.Done():
		streamCancel()
		// Give the stream a short grace to unwind; a hung server must not
		// block the timeout path forever.
		select {
		case streamErr = <-eventErr:
		case <-time.After(2 * time.Second):
		}
	}

	snap := machine.Snapshot()
	res := &Result{
		RunID:     o.RunID,
		SessionID: session.ID,
		ProjectID: projectID,
		MessageID: snap.MessageID,
		AgentSlug: o.AgentSlug,
		Runtime:   session.Runtime,
		Model:     o.ModelID,
		Usage:     snap.Usage,
		StartedAt: started,
		Finished:  time.Now(),
	}

	switch {
	case runCtx.Err() == context.DeadlineExceeded:
		res.Status = "timeout"
		streamCancel()
	case streamErr != nil && !snap.Finished():
		// Stream failed before terminal; surface as protocol/recovery
		// error with the partial state attached.
		return nil, errs.Wrap(errs.KindInternal, streamErr, "stream ended before the turn completed (session %s)", session.ID)
	case snap.Status == turn.StatusActionRequired:
		res.Status = "action_required"
	case snap.ErrorStatus() == turn.StatusError:
		res.Status = "error"
		res.Error = snap.Error
	default:
		res.Status = "completed"
	}
	return res, nil
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
	body := backend.SessionCreateRequest{ProjectID: projectID}
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
	if o.PermissionMode != "" {
		body.PermissionMode = &o.PermissionMode
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
	return &session, nil
}

// handleFrame drives the turn machine. Unmatched/stale frames are ignored.
func (r *Runner) handleFrame(m *turn.Machine, f *backend.SSEFrame, runID, session, project string) error {
	if f.IsHeartbeat() {
		return nil
	}
	et := *f.EventType

	// Anchor on the first message.user of this stream.
	if et == event.EventUser && !m.Snapshot().Anchored {
		m.Anchor(f.Payload["message_id"])
		return nil
	}
	// The user frame is the anchor; everything else must match the turn.
	if !m.Matches(f) {
		return nil
	}

	switch et {
	case event.EventAssistantDelta, event.EventAssistantText:
		d := r.Mapper.DecodeDelta(f.Payload)
		if r.Stdout != nil && d.Text != "" {
			fmt.Fprint(r.Stdout, d.Text)
		}
	case event.EventRunFailed:
		e := r.Mapper.DecodeError(f.Payload)
		m.NoteError(e.Message)
	case event.EventUsage:
		u := r.Mapper.DecodeUsage(f.Payload)
		m.NoteUsage(u.InputTokens, u.OutputTokens, u.CacheReadTokens, u.CacheWriteTokens)
	case event.EventIdle:
		idle := r.Mapper.DecodeIdle(f.Payload)
		m.NoteIdle(idle.StopReason)
	case event.EventRequiresAction:
		m.RequiresAction()
	}
	return nil
}
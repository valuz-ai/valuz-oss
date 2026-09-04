package output

import (
	"bufio"
	"fmt"
	"io"
	"os"

	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
)

// Sink renders RunEvents as JSONL lines, and the terminal RunResult as a
// final JSON document or an extra run.end event line (design.md §6.2:
// "a started run must emit run.end exactly once").
type Sink struct {
	// Format selects the protocol: "" or "human" for no machine output,
	// "json" for the terminal document only, "jsonl" for the event
	// stream with a trailing run.end line.
	Format string
	// TrajectoryPath, when set, mirrors the jsonl event stream to a file
	// (same lines, same redaction).
	TrajectoryPath string
	// Out is the stdout writer for the selected protocol.
	Out io.Writer

	policy RedactPolicy
	file   *os.File
	buf    *bufio.Writer
	// endWritten guards the exactly-once run.end contract.
	endWritten bool
}

// NewSink builds a sink. trajectory is optional. format must be one of
// "", "human", "json", "jsonl"; anything else is a usage error — an
// unknown protocol must fail loudly instead of silently producing no
// output (an eval consumer would misread an empty stdout as success).
func NewSink(format string, out io.Writer, trajectory string) (*Sink, error) {
	switch format {
	case "", "human", "json", "jsonl":
	default:
		return nil, errs.New(errs.KindUsage, "unsupported --output %q (want human|json|jsonl)", format)
	}
	s := &Sink{
		Format:         format,
		TrajectoryPath: trajectory,
		Out:            out,
		policy:         DefaultRedactPolicy(),
	}
	if trajectory != "" {
		f, err := os.Create(trajectory)
		if err != nil {
			return nil, fmt.Errorf("create trajectory file: %w", err)
		}
		s.file = f
		s.buf = bufio.NewWriter(f)
	}
	return s, nil
}

// Close flushes and closes the trajectory file.
func (s *Sink) Close() error {
	if s.buf != nil {
		if err := s.buf.Flush(); err != nil {
			_ = s.file.Close()
			return err
		}
	}
	if s.file != nil {
		return s.file.Close()
	}
	return nil
}

// Event writes one event line under the jsonl protocol (and mirrors it to
// the trajectory file). Human mode is a no-op.
func (s *Sink) Event(e RunEvent) error {
	if s.Format != "jsonl" {
		return nil
	}
	e.Data = s.policy.RedactData(e.Data)
	raw, err := e.Marshal()
	if err != nil {
		return fmt.Errorf("encode event: %w", err)
	}
	line := append(raw, '\n')
	if _, err := s.Out.Write(line); err != nil {
		return err
	}
	if s.buf != nil {
		if _, err := s.buf.Write(line); err != nil {
			return err
		}
	}
	return nil
}

// End emits the terminal document: a single RunResult JSON under "json",
// a run.end event under "jsonl" (exactly once). Returns the exit code
// derived from the result status.
func (s *Sink) End(res RunResult) (int, error) {
	if s.endWritten {
		return ExitCodeFor(res.Status), fmt.Errorf("run.end already emitted")
	}
	s.endWritten = true

	if s.Format == "json" {
		res.FinalMessage = s.policy.RedactField(res.FinalMessage)
		res.Error = s.policy.RedactField(res.Error)
		raw, err := res.Marshal()
		if err != nil {
			return ExitCodeFor(res.Status), fmt.Errorf("encode result: %w", err)
		}
		if _, err := s.Out.Write(append(raw, '\n')); err != nil {
			return ExitCodeFor(res.Status), err
		}
		return ExitCodeFor(res.Status), nil
	}

	if s.Format == "jsonl" {
		res.FinalMessage = s.policy.RedactField(res.FinalMessage)
		res.Error = s.policy.RedactField(res.Error)
		e := RunEvent{
			SchemaVersion: "valuz.run-event/v1",
			RunID:         res.RunID,
			SessionID:     res.SessionID,
			ProjectID:     res.ProjectID,
			MessageID:     res.MessageID,
			Source:        SourceCLI,
			Type:          RunEndType,
			Data:          map[string]any{"result": res},
		}
		if err := s.Event(e); err != nil {
			return ExitCodeFor(res.Status), err
		}
	}
	return ExitCodeFor(res.Status), nil
}

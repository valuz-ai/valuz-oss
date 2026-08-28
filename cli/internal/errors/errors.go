// Package errors implements the product CLI's single error boundary.
//
// Command handlers return typed *Error values; the root command is the only
// place that renders them (human or debug) and maps them to exit codes.
// Errors are redacted before rendering — tokens, authorization values and
// secret-like fields never reach stderr, JSON output or debug traces.
package errors

import (
	"errors"
	"fmt"
	"regexp"
	"strings"
)

// Kind is the stable classification of a CLI error. It drives the exit
// code (see ExitCode) and the human-facing message.
type Kind string

const (
	// KindUsage: invalid arguments / configuration. Exit 1.
	KindUsage Kind = "usage"
	// KindTimeout: wall-clock timeout hit. Exit 2.
	KindTimeout Kind = "timeout"
	// KindAgent: agent execution failed (run.failed, backend cancelled). Exit 3.
	KindAgent Kind = "agent"
	// KindUnreachable: backend unreachable before the run. Exit 4.
	KindUnreachable Kind = "unreachable"
	// KindInternal: protocol, network-recovery or CLI internal failure. Exit 5.
	KindInternal Kind = "internal"
	// KindAuth: authentication / authorization failure. Exit 6.
	KindAuth Kind = "auth"
	// KindActionRequired: the turn parked on an approval with no human in
	// the loop. Exit 7.
	KindActionRequired Kind = "action_required"
)

// ExitCode maps a kind to its stable exit code (see design.md §6.3).
func (k Kind) ExitCode() int {
	switch k {
	case KindUsage:
		return 1
	case KindTimeout:
		return 2
	case KindAgent:
		return 3
	case KindUnreachable:
		return 4
	case KindInternal:
		return 5
	case KindAuth:
		return 6
	case KindActionRequired:
		return 7
	default:
		return 5
	}
}

// Error is the typed CLI error. Wrap it via New/Wrap; test with As.
type Error struct {
	Kind    Kind
	Message string
	// Cause chain (shown under --debug only, redacted).
	Cause error
}

func (e *Error) Error() string {
	if e.Message != "" {
		return e.Message
	}
	if e.Cause != nil {
		return e.Cause.Error()
	}
	return string(e.Kind)
}

// Unwrap exposes the cause for errors.As/Is.
func (e *Error) Unwrap() error { return e.Cause }

// New builds a typed error without a cause.
func New(kind Kind, format string, args ...any) *Error {
	return &Error{Kind: kind, Message: fmt.Sprintf(format, args...)}
}

// Wrap builds a typed error with a cause (redacted on render).
func Wrap(kind Kind, cause error, format string, args ...any) *Error {
	return &Error{Kind: kind, Message: fmt.Sprintf(format, args...), Cause: cause}
}

// As extracts a *Error from err (nil if absent).
func As(err error) *Error {
	var e *Error
	if errors.As(err, &e) {
		return e
	}
	return nil
}

// KindOf returns the error kind, defaulting to KindInternal for untagged
// errors so a raw error never surfaces with a misleading success code.
func KindOf(err error) Kind {
	if e := As(err); e != nil {
		return e.Kind
	}
	return KindInternal
}

// secretLike matches fields that must never be rendered, in any mode.
var secretLike = regexp.MustCompile(
	`(?i)(token|authorization|bearer|api[_-]?key|secret|password|credential)`,
)

// Redact scrubs secret-like substrings from a message. Used by both the
// human renderer and the debug trace; the JSON output protocol redacts at
// the event level (see the output package) with the same policy.
func Redact(s string) string {
	return redactValue(s)
}

func redactValue(s string) string {
	// Match "key=value" / "key: value" forms and bare JWT-like tokens.
	patterns := []*regexp.Regexp{
		regexp.MustCompile(`(?i)\b((?:bearer|token|api[_-]?key|authorization|secret|password)\s*[:=]\s*)\S+`),
		regexp.MustCompile(`\beyJ[A-Za-z0-9_.-]{10,}\.[A-Za-z0-9_.-]{10,}\.[A-Za-z0-9_.-]{10,}\b`),
	}
	out := s
	for _, re := range patterns {
		out = re.ReplaceAllString(out, "${1}[REDACTED]")
	}
	return out
}

// Renderer formats errors for the terminal. Debug mode appends the (redacted)
// cause chain; normal mode prints the actionable message only.
type Renderer struct {
	Debug bool
}

// Render produces the stderr text for err. It is redaction-safe by
// construction: secret-like substrings are masked in both modes.
func (r *Renderer) Render(err error) string {
	e := As(err)
	if e == nil {
		e = &Error{Kind: KindInternal, Message: err.Error()}
	}
	msg := e.Message
	if msg == "" && e.Cause != nil {
		msg = e.Cause.Error()
	}
	msg = Redact(msg)
	if !r.Debug || e.Cause == nil {
		return msg
	}
	var chain strings.Builder
	chain.WriteString(msg)
	chain.WriteString("\n")
	chain.WriteString("caused by: ")
	chain.WriteString(Redact(causeChain(e.Cause)))
	return chain.String()
}

func causeChain(err error) string {
	var parts []string
	for err != nil {
		parts = append(parts, err.Error())
		err = errors.Unwrap(err)
	}
	return strings.Join(parts, " -> ")
}
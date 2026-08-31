package cmd

import (
	"fmt"
	"io"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/config"
	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/output"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/runner"
)

// maxPromptBytes caps --prompt-file / --prompt-stdin reads so a huge
// input cannot OOM the CLI (design review hardening).
const maxPromptBytes = 16 << 20 // 16 MiB

// newRunCmd builds `valuz run`: one connect-only run, one new session,
// one turn (design.md §4.2).
func newRunCmd() *cobra.Command {
	var (
		projectID      string
		cwd            string
		agentSlug      string
		prompt         string
		promptFile     string
		promptStdin    bool
		modelID        string
		providerID     string
		runtimeID      string
		permissionMode string
		timeout        time.Duration
		outputFormat   string
		trajectory     string
		mcpSlugs       []string
		skillIDs       []string
	)

	cmd := &cobra.Command{
		Use:   "run",
		Short: "Run one agent turn in a new session (headless)",
		Long: "Run one agent turn in a new session and wait for it to finish. " +
			"Connect-only: requires a running backend (--backend-url or VALUZ_BACKEND_BASE_URL).",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}

			p, err := resolvePrompt(prompt, promptFile, promptStdin, cmd)
			if err != nil {
				return err
			}

			// SIGINT/SIGTERM cancel the run gracefully; the exit code
			// follows the Unix signal convention (128+signum). The runner
			// classifies a cancelled context as interrupted; the concrete
			// signal name is read back from the channel afterwards (a
			// goroutine-assigned variable would race with the runner).
			sigCh := make(chan os.Signal, 1)
			signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)
			defer signal.Stop(sigCh)
			runCtx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
			defer stop()

			runID := newRunID()
			sink, err := output.NewSink(outputFormat, cmd.OutOrStdout(), trajectory)
			if err != nil {
				return errs.Wrap(errs.KindUsage, err, "init output sink")
			}
			defer sink.Close()

			human := outputFormat == "" || outputFormat == "human"
			token, err := resolveBearer(opts)
			if err != nil {
				return err
			}
			// Run-path defaults: flag > env > profile (model use / agent
			// use / env-based runtime pinning). When an agent is bound
			// (flag or profile), runtime/model/provider derive from the
			// agent's brain (ADR-006) — profile model defaults only apply
			// on the classic model-picker path.
			profile, err := opts.ResolveProfile()
			if err != nil {
				return err
			}
			resolver := config.NewResolver(profile)
			if agentSlug == "" {
				agentSlug = resolver.String("RUN_AGENT_SLUG", "", "")
			}
			if agentSlug == "" {
				if modelID == "" {
					modelID = resolver.String("DEFAULT_MODEL", "", "")
				}
				if providerID == "" {
					providerID = resolver.String("RUN_PROVIDER_ID", "", "")
				}
				if runtimeID == "" {
					runtimeID = resolver.String("DEFAULT_RUNTIME", "", "")
				}
			}
			r := runner.New(
				backend.NewControlClient(opts.BackendURL, token),
				backend.NewStreamClient(opts.BackendURL, token),
				humanWriter(cmd, human),
			)
			res, err := r.Run(runCtx, runner.Options{
				ProjectID:      projectID,
				Cwd:            cwd,
				AgentSlug:      agentSlug,
				ModelID:        modelID,
				ProviderID:     providerID,
				RuntimeID:      runtimeID,
				PermissionMode: permissionMode,
				Prompt:         p,
				Timeout:        timeout,
				RunID:          runID,
				EventSink:      sink,
				HumanOutput:    human,
				MCPSlugs:       mcpSlugs,
				SkillIDs:       skillIDs,
			})
			if err != nil {
				return err
			}
			if res.Status == output.StatusInterrupted {
				// Fill in the concrete signal name for the exit code.
				select {
				case s := <-sigCh:
					switch s {
					case syscall.SIGTERM:
						res.Signal = "SIGTERM"
					default:
						res.Signal = "SIGINT"
					}
				default:
					res.Signal = "SIGINT"
				}
			}
			if human {
				fmt.Fprintln(cmd.OutOrStdout())
				fmt.Fprintf(cmd.OutOrStdout(), "status: %s (session %s, %d tokens in / %d out)\n",
					res.Status, res.SessionID, res.Usage.InputTokens, res.Usage.OutputTokens)
			}
			return runExitError(res)
		},
	}

	f := cmd.Flags()
	f.StringVar(&projectID, "project", "", "backend project id (mutually exclusive with --cwd)")
	f.StringVar(&cwd, "cwd", "", "directory to resolve to a project (default: current dir on loopback)")
	f.StringVar(&agentSlug, "agent", "", "agent slug to bind (SessionCreateRequest.agent_slug)")
	f.StringVar(&prompt, "prompt", "", "prompt text (mutually exclusive with --prompt-file/--prompt-stdin)")
	f.StringVar(&promptFile, "prompt-file", "", "read the prompt from a file")
	f.BoolVar(&promptStdin, "prompt-stdin", false, "read the prompt from stdin")
	f.StringVar(&modelID, "model", "", "model id")
	f.StringVar(&providerID, "provider", "", "provider id")
	f.StringVar(&runtimeID, "runtime", "", "runtime: claude_agent|codex|deepagents|deepseek_harness")
	f.StringVar(&permissionMode, "permission-mode", "", "default|auto_review|full_access (default: default)")
	f.DurationVar(&timeout, "timeout", 0, "wall-clock limit (e.g. 5m); 0 = unlimited")
	f.StringVarP(&outputFormat, flagOutput, "o", "", "output format: human|json|jsonl")
	f.StringVar(&trajectory, "trajectory", "", "mirror the jsonl event stream to a file")
	f.StringSliceVar(&mcpSlugs, "mcp", nil, "MCP data source slugs (repeatable)")
	f.StringSliceVar(&skillIDs, "skill", nil, "extra skill ids to attach (repeatable)")
	return cmd
}

// humanWriter returns a writer for human-mode deltas, or nil when the
// selected protocol is machine-readable (stdout must never mix).
func humanWriter(cmd *cobra.Command, human bool) io.Writer {
	if !human {
		return nil
	}
	return cmd.OutOrStdout()
}

// runExitError maps the run outcome to the process exit code. Interrupted
// runs follow the signal convention; error/timeout/action_required use
// their stable codes (design.md §6.3); completed returns nil.
func runExitError(res *runner.Result) error {
	switch res.Status {
	case output.StatusCompleted:
		return nil
	case output.StatusInterrupted:
		code := 130 // SIGINT default
		if res.Signal == "SIGTERM" {
			code = 143
		}
		return &errs.ExitCodeError{Code: code, Message: "run interrupted by " + res.Signal}
	case output.StatusActionRequired:
		return &errs.ExitCodeError{
			Code:    7,
			Message: "run parked on an approval; use --permission-mode full_access in headless contexts",
		}
	default:
		return &errs.ExitCodeError{
			Code:    output.ExitCodeFor(res.Status),
			Message: fmt.Sprintf("run %s: %s", res.Status, res.Error),
		}
	}
}

// resolvePrompt enforces the three-way exclusivity (design.md §4.2 rule 7):
// automation prefers stdin/file; the TTY is never read implicitly.
func resolvePrompt(prompt, file string, stdin bool, cmd *cobra.Command) (string, error) {
	sources := 0
	if prompt != "" {
		sources++
	}
	if file != "" {
		sources++
	}
	if stdin {
		sources++
	}
	if sources == 0 {
		return "", errs.New(errs.KindUsage, "one of --prompt, --prompt-file or --prompt-stdin is required")
	}
	if sources > 1 {
		return "", errs.New(errs.KindUsage, "--prompt, --prompt-file and --prompt-stdin are mutually exclusive")
	}

	if file != "" {
		data, err := os.ReadFile(file)
		if err != nil {
			return "", errs.Wrap(errs.KindUsage, err, "read --prompt-file %s", file)
		}
		if len(data) == 0 {
			return "", errs.New(errs.KindUsage, "--prompt-file %s is empty", file)
		}
		if len(data) > maxPromptBytes {
			return "", errs.New(errs.KindUsage, "--prompt-file exceeds %d MiB", maxPromptBytes>>20)
		}
		return string(data), nil
	}
	if stdin {
		data, err := io.ReadAll(io.LimitReader(cmd.InOrStdin(), maxPromptBytes+1))
		if err != nil {
			return "", errs.Wrap(errs.KindUsage, err, "read --prompt-stdin")
		}
		if len(data) == 0 {
			return "", errs.New(errs.KindUsage, "--prompt-stdin is empty")
		}
		if len(data) > maxPromptBytes {
			return "", errs.New(errs.KindUsage, "--prompt-stdin exceeds %d MiB", maxPromptBytes>>20)
		}
		return string(data), nil
	}
	return prompt, nil
}

// bearerToken returns the resolved bearer credential from the global
// options (VALUZ_BACKEND_TOKEN env or --token-file).
func bearerToken(opts *RootOptions) string {
	if opts == nil {
		return ""
	}
	return opts.Token
}

func newRunID() string {
	return fmt.Sprintf("run-%d", time.Now().UnixNano())
}

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
	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/output"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/runner"
)

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
			// follows the Unix signal convention (128+signum).
			runCtx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
			defer stop()

			runID := newRunID()
			sink, err := output.NewSink(outputFormat, cmd.OutOrStdout(), trajectory)
			if err != nil {
				return errs.Wrap(errs.KindUsage, err, "init output sink")
			}
			defer sink.Close()

			r := runner.New(
				backend.NewControlClient(opts.BackendURL, bearerToken(opts)),
				backend.NewStreamClient(opts.BackendURL, bearerToken(opts)),
				cmd.OutOrStdout(),
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
			})
			if err != nil {
				return err
			}
			if outputFormat == "human" || outputFormat == "" {
				fmt.Fprintln(cmd.OutOrStdout())
				fmt.Fprintf(cmd.OutOrStdout(), "status: %s (session %s, %d tokens in / %d out)\n",
					res.Status, res.SessionID, res.Usage.InputTokens, res.Usage.OutputTokens)
			}
			if res.Status == output.StatusInterrupted {
				code := 130 // SIGINT default
				if res.Signal == "SIGTERM" {
					code = 143
				}
				return &errs.ExitCodeError{Code: code, Message: "run interrupted by " + res.Signal}
			}
			if res.Status == output.StatusActionRequired {
				return &errs.ExitCodeError{Code: 7, Message: "run parked on an approval; use --permission-mode full_access in headless contexts"}
			}
			return nil
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
	return cmd
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
		return string(data), nil
	}
	if stdin {
		data, err := io.ReadAll(cmd.InOrStdin())
		if err != nil {
			return "", errs.Wrap(errs.KindUsage, err, "read --prompt-stdin")
		}
		if len(data) == 0 {
			return "", errs.New(errs.KindUsage, "--prompt-stdin is empty")
		}
		return string(data), nil
	}
	return prompt, nil
}

// bearerToken is the Slice 2 placeholder: no raw --token flag exists yet;
// the env/token-file path arrives in Slice 5. Keep the wire ready.
func bearerToken(opts *RootOptions) string {
	_ = opts
	return ""
}

func newRunID() string {
	return fmt.Sprintf("run-%d", time.Now().UnixNano())
}

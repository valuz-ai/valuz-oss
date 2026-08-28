package cmd

import (
	"context"
	"fmt"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
)

// newSessionCmd builds `valuz session ...` — session lifecycle around the
// product's conversation model (quick chat / project chat share the same
// session primitive). Wraps the sessions API into functional commands.
func newSessionCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "session",
		Short: "Manage sessions (conversations)",
	}
	cmd.AddCommand(
		newSessionCreateCmd(),
		newSessionListCmd(),
		newSessionShowCmd(),
		newSessionInterruptCmd(),
		newSessionEventsCmd(),
		newSessionSendCmd(),
		newSessionApproveCmd(),
	)
	return cmd
}

func newSessionApproveCmd() *cobra.Command {
	var pendingID, decision, message string
	cmd := &cobra.Command{
		Use:   "approve <id> --pending <pending-id> --decision <verb>",
		Short: "Resolve a pending approval (approve|approve_for_session|reject|answer)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if pendingID == "" {
				return errs.New(errs.KindUsage, "--pending <pending-id> is required")
			}
			switch decision {
			case "approve", "approve_for_session", "reject":
			case "answer":
			case "":
				return errs.New(errs.KindUsage, "--decision is required (approve|approve_for_session|reject|answer)")
			default:
				return errs.New(errs.KindUsage, "unsupported --decision %q", decision)
			}
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			client := backend.NewControlClient(opts.BackendURL, bearerToken(opts))
			var resp struct {
				SessionID string `json:"session_id"`
				PendingID string `json:"pending_id"`
				Decision  string `json:"decision"`
			}
			body := backend.SessionActionRequest{PendingID: pendingID, Decision: decision, Message: message}
			if err := client.Post(cmd.Context(), "/v1/sessions/"+args[0]+"/actions", body, &resp); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "resolved %s (%s)\n", resp.PendingID, resp.Decision)
			return nil
		},
	}
	f := cmd.Flags()
	f.StringVar(&pendingID, "pending", "", "pending approval id (from requires_action events)")
	f.StringVar(&decision, "decision", "", "approve|approve_for_session|reject|answer")
	f.StringVar(&message, "message", "", "optional message with the decision")
	return cmd
}

func newSessionCreateCmd() *cobra.Command {
	var (
		projectID      string
		cwd            string
		agentSlug      string
		modelID        string
		providerID     string
		runtimeID      string
		permissionMode string
		title          string
		quick          bool
		mcpSlugs       []string
		skillIDs       []string
	)
	cmd := &cobra.Command{
		Use:   "create",
		Short: "Create a session (quick chat by default, or bound to a project)",
		RunE: func(cmd *cobra.Command, _ []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			client := backend.NewControlClient(opts.BackendURL, bearerToken(opts))
			_ = quick // --quick is the default fallback; kept for explicitness

			pid, err := idOrQuick(client, cmd.Context(), projectID, cwd)
			if err != nil {
				return err
			}
			s, err := createSession(client, cmd, pid, sessionCreateOpts{
				Title:          title,
				AgentSlug:      agentSlug,
				ModelID:        modelID,
				ProviderID:     providerID,
				RuntimeID:      runtimeID,
				PermissionMode: permissionMode,
				MCPSlugs:       mcpSlugs,
				SkillIDs:       skillIDs,
			})
			if err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "created session %s (project %s, status %s)\n", s.ID, s.ProjectID, s.Status)
			return nil
		},
	}
	f := cmd.Flags()
	f.StringVar(&projectID, "project", "", "project id (default: quick chat)")
	f.StringVar(&cwd, "cwd", "", "resolve project from a local directory")
	f.BoolVar(&quick, "quick", false, "quick chat (default when no project given)")
	f.StringVar(&agentSlug, "agent", "", "agent slug to bind")
	f.StringVar(&modelID, "model", "", "model id")
	f.StringVar(&providerID, "provider", "", "provider id")
	f.StringVar(&runtimeID, "runtime", "", "runtime: claude_agent|codex|deepagents|deepseek_harness")
	f.StringVar(&permissionMode, "permission-mode", "", "default|auto_review|full_access")
	f.StringVar(&title, "title", "", "session title")
	f.StringSliceVar(&mcpSlugs, "mcp", nil, "MCP data source slugs (repeatable)")
	f.StringSliceVar(&skillIDs, "skill", nil, "extra skill ids to attach (repeatable)")
	return cmd
}

func newSessionListCmd() *cobra.Command {
	var projectID string
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List sessions (optionally filtered by project)",
		RunE: func(cmd *cobra.Command, _ []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			client := backend.NewControlClient(opts.BackendURL, bearerToken(opts))
			path := "/v1/sessions"
			if projectID != "" {
				path += "?project_id=" + projectID
			}
			var list backend.SessionListResponse
			if err := client.Get(cmd.Context(), path, &list); err != nil {
				return err
			}
			for _, s := range list.Sessions {
				agent := ""
				if s.AgentSlug != nil {
					agent = *s.AgentSlug
				}
				fmt.Fprintf(cmd.OutOrStdout(), "%-36s  %-10s  %-18s  %s\n", s.ID, s.Status, s.Runtime, agent)
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&projectID, "project", "", "filter by project id")
	return cmd
}

func newSessionShowCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "show <id>",
		Short: "Show a session's detail",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			client := backend.NewControlClient(opts.BackendURL, bearerToken(opts))
			var s backend.SessionDetail
			if err := client.Get(cmd.Context(), "/v1/sessions/"+args[0], &s); err != nil {
				return err
			}
			agent := ""
			if s.AgentSlug != nil {
				agent = *s.AgentSlug
			}
			model := ""
			if s.ModelID != nil {
				model = *s.ModelID
			}
			fmt.Fprintf(cmd.OutOrStdout(), "id:             %s\n", s.ID)
			fmt.Fprintf(cmd.OutOrStdout(), "project:        %s\n", s.ProjectID)
			fmt.Fprintf(cmd.OutOrStdout(), "status:         %s\n", s.Status)
			fmt.Fprintf(cmd.OutOrStdout(), "runtime:        %s\n", s.Runtime)
			fmt.Fprintf(cmd.OutOrStdout(), "permission:     %s\n", s.PermissionMode)
			fmt.Fprintf(cmd.OutOrStdout(), "agent:          %s\n", agent)
			fmt.Fprintf(cmd.OutOrStdout(), "model:          %s\n", model)
			return nil
		},
	}
}

func newSessionInterruptCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "interrupt <id>",
		Short: "Interrupt a running session",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			client := backend.NewControlClient(opts.BackendURL, bearerToken(opts))
			var s backend.SessionDetail
			if err := client.Post(cmd.Context(), "/v1/sessions/"+args[0]+"/interrupt", nil, &s); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "interrupted session %s (status %s)\n", s.ID, s.Status)
			return nil
		},
	}
}

func newSessionEventsCmd() *cobra.Command {
	var outputFormat string
	var stream bool
	cmd := &cobra.Command{
		Use:   "events <id>",
		Short: "Show session events (durable history, or live stream)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			sessionID := args[0]
			client := backend.NewControlClient(opts.BackendURL, bearerToken(opts))
			streamClient := backend.NewStreamClient(opts.BackendURL, bearerToken(opts))
			streamClient.ReconnectMaxAttempts = 0

			if stream {
				return streamClient.Stream(cmd.Context(), "/v1/sessions/"+sessionID+"/events/stream", 0, func(ctx context.Context, f *backend.SSEFrame) error {
					if f.IsHeartbeat() {
						return nil
					}
					if outputFormat == "jsonl" {
						raw, err := frameJSON(f)
						if err != nil {
							return err
						}
						fmt.Fprintln(cmd.OutOrStdout(), string(raw))
						return nil
					}
					fmt.Fprintf(cmd.OutOrStdout(), "%s %s\n", *f.EventType, payloadSummary(f))
					return nil
				})
			}

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
			if err := client.Get(cmd.Context(), "/v1/sessions/"+sessionID+"/events", &history); err != nil {
				return err
			}
			for _, it := range history.Items {
				line := fmt.Sprintf("%d %s", it.Seq, it.Event.EventType)
				if t := it.Event.Payload["text"]; t != "" {
					line += " " + truncate(t, 80)
				}
				fmt.Fprintln(cmd.OutOrStdout(), line)
			}
			return nil
		},
	}
	cmd.Flags().StringVarP(&outputFormat, flagOutput, "o", "", "output format: human|jsonl")
	cmd.Flags().BoolVar(&stream, "stream", false, "subscribe live events (SSE)")
	return cmd
}

func newSessionSendCmd() *cobra.Command {
	var prompt string
	cmd := &cobra.Command{
		Use:   "send <id>",
		Short: "Send a message to a session (non-blocking dispatch)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if prompt == "" {
				return errs.New(errs.KindUsage, "--prompt is required")
			}
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			client := backend.NewControlClient(opts.BackendURL, bearerToken(opts))
			var s backend.SessionDetail
			if err := client.Post(cmd.Context(), "/v1/sessions/"+args[0]+"/messages",
				backend.SessionMessageRequest{Prompt: prompt}, &s); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "dispatched (session %s, status %s)\n", s.ID, s.Status)
			return nil
		},
	}
	cmd.Flags().StringVar(&prompt, "prompt", "", "message to send")
	return cmd
}

func payloadSummary(f *backend.SSEFrame) string {
	if t := f.Payload["text"]; t != "" {
		return truncate(t, 80)
	}
	if m := f.Payload["message"]; m != "" {
		return truncate(m, 80)
	}
	return ""
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

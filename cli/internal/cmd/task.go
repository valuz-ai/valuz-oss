package cmd

import (
	"context"
	"fmt"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
)

// newTaskCmd builds `valuz task ...` — lead-orchestrated multi-agent tasks
// (the product's Task shape: a goal dispatched to a lead agent that plans
// and delegates to member agents). Functional wrappers over the tasks API.
func newTaskCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "task",
		Short: "Manage tasks (lead-orchestrated multi-agent work)",
	}
	cmd.AddCommand(
		newTaskKickoffCmd(),
		newTaskListCmd(),
		newTaskShowCmd(),
		newTaskEventsCmd(),
		newTaskInterveneCmd(),
		newTaskCommitCmd(),
		newTaskAbandonCmd(),
		newTaskInjectCmd(),
		newTaskPlanCmd(),
	)
	return cmd
}

func newTaskKickoffCmd() *cobra.Command {
	var (
		projectID string
		cwd       string
		title     string
		worktree  bool
	)
	cmd := &cobra.Command{
		Use:   "kickoff",
		Short: "Start a new task: dispatch a goal to a lead agent",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			goal, _ := cmd.Flags().GetString("goal")
			lead, _ := cmd.Flags().GetString("lead")
			if goal == "" || lead == "" {
				return errs.New(errs.KindUsage, "--goal and --lead are required")
			}
			token, err := resolveBearer(opts)
			if err != nil {
				return err
			}
			client := newControlClient(opts, token)
			pid, err := idOrResolve(client, cmd.Context(), projectID, cwd)
			if err != nil {
				return err
			}
			var task backend.Task
			body := backend.KickoffTaskRequest{
				Goal:          goal,
				LeadAgentSlug: lead,
				Title:         title,
				Worktree:      worktree,
			}
			if err := client.Post(cmd.Context(), "/v1/projects/"+pid+"/tasks", body, &task); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "task %s kicked off (status %s, lead %s)\n",
				task.ID, task.Status, task.LeadAgentSlug)
			return nil
		},
	}
	f := cmd.Flags()
	f.StringVar(&projectID, "project", "", "project id")
	f.StringVar(&cwd, "cwd", "", "resolve project from a local directory")
	f.String("goal", "", "task goal")
	f.String("lead", "", "lead agent slug")
	f.StringVar(&title, "title", "", "task title")
	f.BoolVar(&worktree, "worktree", false, "run in an isolated git worktree")
	return cmd
}

func newTaskListCmd() *cobra.Command {
	var limit int
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List tasks",
		RunE: func(cmd *cobra.Command, _ []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			token, err := resolveBearer(opts)
			if err != nil {
				return err
			}
			client := newControlClient(opts, token)
			var list backend.TaskListResponse
			if err := client.Get(cmd.Context(), "/v1/tasks?limit="+fmt.Sprint(limit), &list); err != nil {
				return err
			}
			for _, t := range list.Tasks {
				fmt.Fprintf(cmd.OutOrStdout(), "%-36s  %-10s  %-18s  %s\n", t.ID, t.Status, t.LeadAgentSlug, truncate(t.Title, 40))
			}
			return nil
		},
	}
	cmd.Flags().IntVar(&limit, "limit", 50, "max tasks (1-200)")
	return cmd
}

func newTaskShowCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "show <id>",
		Short: "Show a task with its runs and events",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			token, err := resolveBearer(opts)
			if err != nil {
				return err
			}
			client := newControlClient(opts, token)
			var detail backend.TaskDetail
			if err := client.Get(cmd.Context(), "/v1/tasks/"+args[0], &detail); err != nil {
				return err
			}
			w := cmd.OutOrStdout()
			fmt.Fprintf(w, "id:         %s\n", detail.Task.ID)
			fmt.Fprintf(w, "title:      %s\n", detail.Task.Title)
			fmt.Fprintf(w, "status:     %s\n", detail.Task.Status)
			fmt.Fprintf(w, "lead:       %s\n", detail.Task.LeadAgentSlug)
			fmt.Fprintf(w, "holder:     %s\n", detail.Task.CurrentHolder)
			fmt.Fprintf(w, "goal:       %s\n", truncate(detail.Task.Goal, 100))
			fmt.Fprintf(w, "runs:       %d\n", len(detail.Runs))
			for _, r := range detail.Runs {
				fmt.Fprintf(w, "  run %-36s  %-6s  %-14s  %s\n", r.ID, r.Kind, r.Status, r.AgentSlug)
			}
			fmt.Fprintf(w, "events:     %d\n", len(detail.Events))
			return nil
		},
	}
}

func newTaskEventsCmd() *cobra.Command {
	var stream bool
	cmd := &cobra.Command{
		Use:   "events <id>",
		Short: "Show task timeline events (or live stream)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			token, err := resolveBearer(opts)
			if err != nil {
				return err
			}
			client := newControlClient(opts, token)
			if stream {
				return streamTaskEvents(cmd, opts, args[0])
			}
			var resp struct {
				Events []backend.TaskEvent `json:"events"`
			}
			if err := client.Get(cmd.Context(), "/v1/tasks/"+args[0]+"/events", &resp); err != nil {
				return err
			}
			for _, e := range resp.Events {
				fmt.Fprintf(cmd.OutOrStdout(), "#%-5d  %-24s  %s\n", e.Sequence, e.Type, e.Actor)
			}
			return nil
		},
	}
	cmd.Flags().BoolVar(&stream, "stream", false, "subscribe to live task events")
	return cmd
}

// streamTaskEvents subscribes the task SSE stream (polling-based backend
// stream with id: cursor and stream_end semantics).
func streamTaskEvents(cmd *cobra.Command, opts *RootOptions, taskID string) error {
	token, err := resolveBearer(opts)
	if err != nil {
		return err
	}
	client := newControlClient(opts, token)
	streamClient := newStreamClient(opts, token)
	streamClient.ReconnectMaxAttempts = 0

	var afterSeq int64
	for {
		err := streamClient.Stream(cmd.Context(), "/v1/tasks/"+taskID+"/events/stream", afterSeq, func(ctx context.Context, f *backend.SSEFrame) error {
			if f.IsHeartbeat() {
				return nil
			}
			fmt.Fprintf(cmd.OutOrStdout(), "%s\n", *f.EventType)
			return nil
		})
		if err != nil {
			// Task streams close with stream_end after terminal events;
			// treat a clean close as done. Reconnect with the last cursor
			// when the task is still active.
			if cmd.Context().Err() != nil {
				return nil
			}
			var detail backend.TaskDetail
			if gerr := client.Get(cmd.Context(), "/v1/tasks/"+taskID, &detail); gerr != nil {
				return err
			}
			if detail.Task.Status == "completed" || detail.Task.Status == "abandoned" {
				return nil
			}
			return err
		}
		return nil
	}
}

func newTaskInterveneCmd() *cobra.Command {
	var action, goal, note string
	cmd := &cobra.Command{
		Use:   "intervene <id> --action <pause|resume|stop|note|revise-goal>",
		Short: "Intervene in a running task (pause/resume/stop/note/revise goal)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			if action == "" {
				return errs.New(errs.KindUsage, "--action is required (pause|resume|stop|note|revise-goal)")
			}
			token, err := resolveBearer(opts)
			if err != nil {
				return err
			}
			client := newControlClient(opts, token)
			var task backend.Task
			body := backend.TaskInterveneRequest{Action: action}
			switch action {
			case "note":
				body.Text = note
			case "revise-goal":
				body.Goal = goal
			}
			if err := client.Post(cmd.Context(), "/v1/tasks/"+args[0]+":intervene", body, &task); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "task %s -> %s\n", task.ID, task.Status)
			return nil
		},
	}
	f := cmd.Flags()
	f.StringVar(&action, "action", "", "pause|resume|stop|note|revise-goal")
	f.StringVar(&goal, "goal", "", "new goal (revise-goal)")
	f.StringVar(&note, "note", "", "note text (note)")
	return cmd
}

func newTaskCommitCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "commit <id>",
		Short: "Commit a drafted task (activate the plan)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			token, err := resolveBearer(opts)
			if err != nil {
				return err
			}
			client := newControlClient(opts, token)
			var resp struct {
				TaskID        string `json:"task_id"`
				LeadSessionID string `json:"lead_session_id"`
				Status        string `json:"status"`
			}
			if err := client.Post(cmd.Context(), "/v1/tasks/"+args[0]+":commit", nil, &resp); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "task %s committed (status %s, lead session %s)\n",
				resp.TaskID, resp.Status, resp.LeadSessionID)
			return nil
		},
	}
}

func newTaskAbandonCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "abandon <id>",
		Short: "Abandon a task",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			token, err := resolveBearer(opts)
			if err != nil {
				return err
			}
			client := newControlClient(opts, token)
			var resp struct {
				TaskID string `json:"task_id"`
				Status string `json:"status"`
			}
			if err := client.Post(cmd.Context(), "/v1/tasks/"+args[0]+":abandon", nil, &resp); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "task %s -> %s\n", resp.TaskID, resp.Status)
			return nil
		},
	}
}

func newTaskInjectCmd() *cobra.Command {
	var text string
	cmd := &cobra.Command{
		Use:   "inject <id> --text <msg>",
		Short: "Inject a message into a running task (e.g. redirect the lead)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if text == "" {
				return errs.New(errs.KindUsage, "--text is required")
			}
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			token, err := resolveBearer(opts)
			if err != nil {
				return err
			}
			client := newControlClient(opts, token)
			var resp struct {
				Delivered bool `json:"delivered"`
			}
			body := map[string]string{"text": text}
			if err := client.Post(cmd.Context(), "/v1/tasks/"+args[0]+":inject", body, &resp); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "injected (delivered=%v)\n", resp.Delivered)
			return nil
		},
	}
	cmd.Flags().StringVar(&text, "text", "", "message to inject")
	return cmd
}

func newTaskPlanCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "plan <id>",
		Short: "Show a task's plan",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			token, err := resolveBearer(opts)
			if err != nil {
				return err
			}
			client := newControlClient(opts, token)
			var plan backend.TaskPlan
			if err := client.Get(cmd.Context(), "/v1/tasks/"+args[0]+"/plan", &plan); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "plan ready=%v version=%d subtasks=%d\n",
				plan.Ready, plan.CurrentVersion, len(plan.Subtasks))
			return nil
		},
	}
}

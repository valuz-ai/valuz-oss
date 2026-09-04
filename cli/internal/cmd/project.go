package cmd

import (
	"fmt"
	"path/filepath"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
)

// newProjectCmd builds `valuz project ...` — workspace (project) management.
// Projects are the agent workspace root: quick chats live in the
// auto-minted "chat-default" project, working sessions bind to a project
// directory. Commands are functional wrappers over the projects API, not
// bare endpoint mirrors.
func newProjectCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "project",
		Short: "Manage projects (workspaces)",
	}
	cmd.AddCommand(
		newProjectListCmd(),
		newProjectShowCmd(),
		newProjectCreateCmd(),
		newProjectMembersCmd(),
		newProjectDeployCmd(),
	)
	return cmd
}

func newProjectMembersCmd() *cobra.Command {
	var cwd string
	cmd := &cobra.Command{
		Use:   "members <id|chat-default>",
		Short: "List a project's agent members",
		Args:  cobra.MaximumNArgs(1),
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
			pid := ""
			if len(args) == 1 {
				pid = args[0]
			} else {
				pid, err = idOrResolve(client, cmd.Context(), "", cwd)
				if err != nil {
					return err
				}
			}
			var resp struct {
				Agents []struct {
					Member struct {
						AgentSlug string `json:"agent_slug"`
					} `json:"member"`
					Agent struct {
						Name            string `json:"name"`
						RuntimeProvider string `json:"runtime_provider"`
						Model           string `json:"model"`
					} `json:"agent"`
				} `json:"agents"`
			}
			if err := client.Get(cmd.Context(), "/v1/projects/"+pid+"/agents", &resp); err != nil {
				return err
			}
			for _, m := range resp.Agents {
				fmt.Fprintf(cmd.OutOrStdout(), "%-28s  %-10s  %s\n", m.Member.AgentSlug, m.Agent.RuntimeProvider, m.Agent.Model)
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&cwd, "cwd", "", "resolve the project from a local directory")
	return cmd
}

func newProjectDeployCmd() *cobra.Command {
	var (
		projectID string
		cwd       string
		agentSlug string
		localSlug string
	)
	cmd := &cobra.Command{
		Use:   "deploy --agent <library-slug>",
		Short: "Deploy a library agent into a project as a member",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if agentSlug == "" {
				return errs.New(errs.KindUsage, "--agent <slug> is required")
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
			pid, err := idOrResolve(client, cmd.Context(), projectID, cwd)
			if err != nil {
				return err
			}
			body := map[string]any{"source_agent_slug": agentSlug}
			if localSlug != "" {
				body["agent_slug"] = localSlug
			}
			var member struct {
				Member struct {
					AgentSlug string `json:"agent_slug"`
				} `json:"member"`
				Agent struct {
					RuntimeProvider string `json:"runtime_provider"`
				} `json:"agent"`
			}
			if err := client.Post(cmd.Context(), "/v1/projects/"+pid+"/agents:deploy", body, &member); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "deployed %s -> %s (runtime %s)\n",
				agentSlug, member.Member.AgentSlug, member.Agent.RuntimeProvider)
			return nil
		},
	}
	f := cmd.Flags()
	f.StringVar(&projectID, "project", "", "project id")
	f.StringVar(&cwd, "cwd", "", "resolve project from a local directory")
	f.StringVar(&agentSlug, "agent", "", "library agent slug to deploy")
	f.StringVar(&localSlug, "as", "", "project-local slug (default: derived from agent name)")
	return cmd
}

func newProjectListCmd() *cobra.Command {
	var output string
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List projects",
		RunE: func(cmd *cobra.Command, _ []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
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
			var list backend.ProjectList
			if err := client.Get(cmd.Context(), "/v1/projects", &list); err != nil {
				return err
			}
			if printJSONOutput(cmd.OutOrStdout(), output, list.Projects) {
				return nil
			}
			for _, p := range list.Projects {
				fmt.Fprintf(cmd.OutOrStdout(), "%-36s  %-16s  %s\n", p.ID, p.Name, p.RootPath)
			}
			return nil
		},
	}
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

func newProjectShowCmd() *cobra.Command {
	var cwd string
	cmd := &cobra.Command{
		Use:   "show <id|chat-default>",
		Short: "Show a project by id (or resolve from a local directory)",
		Args:  cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			id := ""
			if len(args) == 1 {
				id = args[0]
			}
			token, err := resolveBearer(opts)
			if err != nil {
				return err
			}
			client := newControlClient(opts, token)

			// Resolve id from cwd when no positional id is given (same
			// normalization the run path uses, so `--cwd` and
			// `project show --cwd` agree).
			if id == "" {
				if cwd == "" {
					return errs.New(errs.KindUsage, "provide a project id or --cwd")
				}
				pid, err := resolveProjectID(cmd, client, cwd)
				if err != nil {
					return err
				}
				id = pid
			}

			var detail backend.Project
			if err := client.Get(cmd.Context(), "/v1/projects/"+id, &detail); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "id:        %s\n", detail.ID)
			fmt.Fprintf(cmd.OutOrStdout(), "name:      %s\n", detail.Name)
			fmt.Fprintf(cmd.OutOrStdout(), "root_path: %s\n", detail.RootPath)
			return nil
		},
	}
	cmd.Flags().StringVar(&cwd, "cwd", "", "resolve the project from a local directory")
	return cmd
}

func newProjectCreateCmd() *cobra.Command {
	var cwd string
	cmd := &cobra.Command{
		Use:   "create <name>",
		Short: "Create a project bound to a directory",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			if cwd == "" {
				cwd = "."
			}
			abs, err := filepath.Abs(cwd)
			if err != nil {
				return errs.Wrap(errs.KindUsage, err, "resolve --cwd")
			}
			token, err := resolveBearer(opts)
			if err != nil {
				return err
			}
			client := newControlClient(opts, token)
			var created backend.Project
			body := backend.Project{Name: args[0], RootPath: abs}
			if err := client.Post(cmd.Context(), "/v1/projects", body, &created); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "created project %s (root: %s)\n", created.ID, created.RootPath)
			return nil
		},
	}
	cmd.Flags().StringVar(&cwd, "cwd", "", "directory to bind (default: current dir)")
	return cmd
}

// resolveProjectID normalizes a local directory to a project id using the
// same lookup-or-create semantics as the run path.
func resolveProjectID(cmd *cobra.Command, client *backend.ControlClient, cwd string) (string, error) {
	return resolveProjectWith(client, cmd.Context(), cwd)
}

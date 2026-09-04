package cmd

import (
	"fmt"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
)

// newAgentCmd builds the top-level `valuz agent ...` command — agent
// selection for the run path (list/show browse the same catalog as
// `valuz resource agents/agent`, use pins the default).
func newAgentCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "agent",
		Short: "Select the default agent for run (browse: valuz resource)",
	}
	cmd.AddCommand(
		newAgentListCmd(),
		newAgentShowCmd(),
		newAgentUseCmd(),
	)
	return cmd
}

// newAgentListCmd lists the agent catalog (same backend surface as
// `valuz resource agents`; kept on the selection command so the product
// command surface matches the docs: list → show → use).
func newAgentListCmd() *cobra.Command {
	var source, output string
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List agents (execution identities: skills/connectors/instructions)",
		RunE: func(cmd *cobra.Command, _ []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			return runAgentList(cmd, opts, source, output)
		},
	}
	cmd.Flags().StringVar(&source, "source", "", "official|custom")
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

// newAgentShowCmd shows one agent's full profile.
func newAgentShowCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "show <slug>",
		Short: "Show an agent's full profile (skills, connectors, model)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			return runAgentShow(cmd, opts, args[0])
		},
	}
}

// runAgentList is the shared list implementation (agent list / resource agents).
func runAgentList(cmd *cobra.Command, opts *RootOptions, source, output string) error {
	if opts == nil {
		return errs.New(errs.KindInternal, "agent list requires resolved options")
	}
	token, err := resolveBearer(opts)
	if err != nil {
		return err
	}
	client := newControlClient(opts, token)
	path := "/v1/agents"
	if source != "" {
		path += "?source=" + source
	}
	var resp backend.AgentListResponse
	if err := client.Get(cmd.Context(), path, &resp); err != nil {
		return err
	}
	if printJSONOutput(cmd.OutOrStdout(), output, resp.Agents) {
		return nil
	}
	for _, a := range resp.Agents {
		fmt.Fprintf(cmd.OutOrStdout(), "%-28s  %-10s  %-14s  %-18s  %s\n",
			a.Slug, a.Runtime, a.Model, truncate(a.Name, 18), truncate(a.Description, 40))
	}
	return nil
}

// runAgentShow is the shared show implementation (agent show / resource agent).
func runAgentShow(cmd *cobra.Command, opts *RootOptions, slug string) error {
	if opts == nil {
		return errs.New(errs.KindInternal, "agent show requires resolved options")
	}
	token, err := resolveBearer(opts)
	if err != nil {
		return err
	}
	client := newControlClient(opts, token)
	var a backend.Agent
	if err := client.Get(cmd.Context(), "/v1/agents/"+slug, &a); err != nil {
		return err
	}
	w := cmd.OutOrStdout()
	fmt.Fprintf(w, "slug:       %s\n", a.Slug)
	fmt.Fprintf(w, "name:       %s\n", a.Name)
	fmt.Fprintf(w, "runtime:    %s\n", a.Runtime)
	fmt.Fprintf(w, "model:      %s\n", a.Model)
	fmt.Fprintf(w, "permission: %s\n", a.PermissionMode)
	fmt.Fprintf(w, "skills:     %v\n", a.Skills)
	fmt.Fprintf(w, "connectors: %v\n", a.ConnectorTypes)
	if a.Description != "" {
		fmt.Fprintf(w, "desc:       %s\n", truncate(a.Description, 120))
	}
	return nil
}

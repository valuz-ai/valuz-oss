package cmd

import (
	"fmt"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
)

// newResourceCmd builds `valuz resource ...` — the workspace resource
// library (mobile "资源库": Agents / Skills / Connectors; view, enable,
// disable and status confirmation only — complex create/edit stays in the
// desktop/web UI).
func newResourceCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "resource",
		Short: "Browse workspace resources (agents, skills, connectors)",
	}
	cmd.AddCommand(
		newResourceAgentsCmd(),
		newResourceAgentShowCmd(),
		newResourceSkillsCmd(),
		newResourceConnectorsCmd(),
	)
	return cmd
}

func newResourceAgentsCmd() *cobra.Command {
	var source string
	cmd := &cobra.Command{
		Use:   "agents",
		Short: "List agents (execution identities: skills/connectors/instructions)",
		RunE: func(cmd *cobra.Command, _ []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			path := "/v1/agents"
			if source != "" {
				path += "?source=" + source
			}
			client := backend.NewControlClient(opts.BackendURL, bearerToken(opts))
			var resp backend.AgentListResponse
			if err := client.Get(cmd.Context(), path, &resp); err != nil {
				return err
			}
			for _, a := range resp.Agents {
				fmt.Fprintf(cmd.OutOrStdout(), "%-28s  %-10s  %-14s  %-18s  %s\n",
					a.Slug, a.Runtime, a.Model, truncate(a.Name, 18), truncate(a.Description, 40))
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&source, "source", "", "official|custom")
	return cmd
}

func newResourceAgentShowCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "agent <slug>",
		Short: "Show an agent's full profile (skills, connectors, model)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			client := backend.NewControlClient(opts.BackendURL, bearerToken(opts))
			var a backend.Agent
			if err := client.Get(cmd.Context(), "/v1/agents/"+args[0], &a); err != nil {
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
		},
	}
}

func newResourceSkillsCmd() *cobra.Command {
	var category string
	cmd := &cobra.Command{
		Use:   "skills",
		Short: "List skills",
		RunE: func(cmd *cobra.Command, _ []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			path := "/v1/skills"
			if category != "" {
				path += "?category=" + category
			}
			client := backend.NewControlClient(opts.BackendURL, bearerToken(opts))
			var resp backend.SkillListResponse
			if err := client.Get(cmd.Context(), path, &resp); err != nil {
				return err
			}
			for _, s := range resp.Skills {
				enabled := "-"
				if s.Enabled {
					enabled = "on"
				}
				fmt.Fprintf(cmd.OutOrStdout(), "%-28s  %-12s  %-3s  %s\n",
					s.Slug, s.Category, enabled, truncate(s.Description, 50))
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&category, "category", "", "filter by category")
	return cmd
}

func newResourceConnectorsCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "connectors",
		Short: "List connectors (MCP data sources)",
		RunE: func(cmd *cobra.Command, _ []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			client := backend.NewControlClient(opts.BackendURL, bearerToken(opts))
			var resp backend.ConnectorListResponse
			if err := client.Get(cmd.Context(), "/v1/connectors", &resp); err != nil {
				return err
			}
			for _, c := range resp.Connectors {
				enabled := "-"
				if c.Enabled {
					enabled = "on"
				}
				fmt.Fprintf(cmd.OutOrStdout(), "%-28s  %-14s  %-3s  %s\n",
					c.Slug, c.ConnectorType, enabled, truncate(c.Description, 50))
			}
			return nil
		},
	}
	return cmd
}

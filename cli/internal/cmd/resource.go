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
	var source, output string
	cmd := &cobra.Command{
		Use:   "agents",
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
			return runAgentShow(cmd, opts, args[0])
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
			token, err := resolveBearer(opts)
			if err != nil {
				return err
			}
			client := newControlClient(opts, token)
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
			token, err := resolveBearer(opts)
			if err != nil {
				return err
			}
			client := newControlClient(opts, token)
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

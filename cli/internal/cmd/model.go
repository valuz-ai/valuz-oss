package cmd

import (
	"fmt"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/config"
)

// newModelCmd builds `valuz model ...` — model discovery and selection.
// Models come from the backend's provider catalog (each provider exposes
// its models with the runtimes/protocols they serve); `model use` pins
// the default for the run path in the active profile.
func newModelCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "model",
		Short: "Discover and select models",
	}
	cmd.AddCommand(
		newModelListCmd(),
		newModelUseCmd(),
	)
	return cmd
}

func newModelListCmd() *cobra.Command {
	var (
		runtime  string
		provider string
		output   string
	)
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List available models (filter by runtime/provider)",
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
			var resp struct {
				Providers []struct {
					ID     string `json:"id"`
					Name   string `json:"name"`
					Models []struct {
						ID       string   `json:"id"`
						Label    string   `json:"label"`
						Runtimes []string `json:"runtimes"`
					} `json:"models"`
				} `json:"providers"`
			}
			if err := client.Get(cmd.Context(), "/v1/providers", &resp); err != nil {
				return err
			}
			if output == "json" {
				type modelRow struct {
					ID       string   `json:"id"`
					Label    string   `json:"label"`
					Provider string   `json:"provider"`
					Runtimes []string `json:"runtimes"`
				}
				var rows []modelRow
				for _, p := range resp.Providers {
					for _, m := range p.Models {
						if runtime != "" && !contains(m.Runtimes, runtime) {
							continue
						}
						if provider != "" && p.ID != provider {
							continue
						}
						rows = append(rows, modelRow{ID: m.ID, Label: m.Label, Provider: p.ID, Runtimes: m.Runtimes})
					}
				}
				if printJSONOutput(cmd.OutOrStdout(), output, rows) {
					return nil
				}
			}
			for _, p := range resp.Providers {
				for _, m := range p.Models {
					if runtime != "" && !contains(m.Runtimes, runtime) {
						continue
					}
					if provider != "" && p.ID != provider {
						continue
					}
					fmt.Fprintf(cmd.OutOrStdout(), "%-32s  %-24s  %-16s  %v\n", m.ID, m.Label, p.ID, m.Runtimes)
				}
			}
			return nil
		},
	}
	f := cmd.Flags()
	f.StringVar(&runtime, "runtime", "", "filter by runtime (claude_agent|codex|deepagents|deepseek_harness)")
	f.StringVar(&provider, "provider", "", "filter by provider id")
	f.StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

func newModelUseCmd() *cobra.Command {
	var (
		providerID string
		runtimeID  string
	)
	cmd := &cobra.Command{
		Use:   "use <model-id>",
		Short: "Pin the default model (and provider/runtime) for run",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			profile, err := opts.ResolveProfile()
			if err != nil {
				return err
			}
			if profile.Defaults == nil {
				profile.Defaults = map[string]string{}
			}
			profile.Defaults["DEFAULT_MODEL"] = args[0]
			if providerID != "" {
				profile.Defaults["RUN_PROVIDER_ID"] = providerID
			}
			if runtimeID != "" {
				profile.Defaults["DEFAULT_RUNTIME"] = runtimeID
			}
			store := config.NewProfileStore("")
			if err := store.Save(profile); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "default model -> %s", args[0])
			if providerID != "" {
				fmt.Fprintf(cmd.OutOrStdout(), " (provider %s)", providerID)
			}
			if runtimeID != "" {
				fmt.Fprintf(cmd.OutOrStdout(), " (runtime %s)", runtimeID)
			}
			fmt.Fprintln(cmd.OutOrStdout())
			return nil
		},
	}
	f := cmd.Flags()
	f.StringVar(&providerID, "provider", "", "provider id to pin alongside the model")
	f.StringVar(&runtimeID, "runtime", "", "runtime to pin alongside the model")
	return cmd
}

// newAgentUseCmd builds `valuz agent use <slug>` — pin the default agent
// for the run path (binds skills/connectors/instructions + runtime/model
// defaults per ADR-006).
func newAgentUseCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "use <slug>",
		Short: "Pin the default agent for run (binds skills/connectors/instructions)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			profile, err := opts.ResolveProfile()
			if err != nil {
				return err
			}
			if profile.Defaults == nil {
				profile.Defaults = map[string]string{}
			}
			profile.Defaults["RUN_AGENT_SLUG"] = args[0]
			store := config.NewProfileStore("")
			if err := store.Save(profile); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "default agent -> %s\n", args[0])
			return nil
		},
	}
}

func contains(items []string, want string) bool {
	for _, it := range items {
		if it == want {
			return true
		}
	}
	return false
}

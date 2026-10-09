package cmd

import (
	"context"
	"fmt"
	"io"
	"net/url"
	"sort"
	"strings"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
)

func newBuiltinPluginCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "builtin",
		Short: "View and toggle built-in backend plugins",
		Long: "Built-in backend plugins ship with Valuz. Enable/disable changes\n" +
			"take effect at the next start; required plugins cannot be disabled.",
	}
	cmd.AddCommand(newBuiltinPluginCommands()...)
	return cmd
}

func newBuiltinPluginCommands() []*cobra.Command {
	return []*cobra.Command{newBuiltinPluginListCmd(), newBuiltinPluginToggleCmd("enable", true), newBuiltinPluginToggleCmd("disable", false)}
}

type builtinPluginItem struct {
	ID             string   `json:"id"`
	Status         string   `json:"status"`
	Required       bool     `json:"required"`
	Needs          []string `json:"needs"`
	Provides       []string `json:"provides"`
	Entitlement    *string  `json:"entitlement"`
	Error          *string  `json:"error"`
	DesiredEnabled bool     `json:"desiredEnabled"`
}

type builtinPluginList struct {
	Composed bool                `json:"composed"`
	Editable bool                `json:"editable"`
	Plugins  []builtinPluginItem `json:"plugins"`
}

// pluginClient resolves the bearer and builds the control client once per command.
func pluginClient(cmd *cobra.Command) (*backend.ControlClient, error) {
	opts, err := Options(cmd)
	if err != nil {
		return nil, err
	}
	token, err := resolveBearer(opts)
	if err != nil {
		return nil, err
	}
	return newControlClient(opts, token), nil
}

func newBuiltinPluginListCmd() *cobra.Command {
	var output string
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List built-in backend plugins and their status",
		RunE: func(cmd *cobra.Command, _ []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			client, err := pluginClient(cmd)
			if err != nil {
				return err
			}
			var resp builtinPluginList
			if err := client.Get(cmd.Context(), "/v1/builtin-plugins", &resp); err != nil {
				return err
			}
			if printJSONOutput(cmd.OutOrStdout(), output, resp) {
				return nil
			}
			printBuiltinPlugins(cmd.OutOrStdout(), resp)
			return nil
		},
	}
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

func printBuiltinPlugins(out io.Writer, resp builtinPluginList) {
	if !resp.Composed {
		fmt.Fprintln(out, "(this build has no built-in backend plugins)")
		return
	}
	if len(resp.Plugins) == 0 {
		fmt.Fprintln(out, "(no built-in backend plugins)")
		return
	}
	pending := false
	for _, p := range resp.Plugins {
		flags := []string{}
		if p.Required {
			flags = append(flags, "required")
		}
		running := p.Status != "disabled" && p.Status != "disposed"
		if !p.Required && p.DesiredEnabled != running {
			pending = true
			if p.DesiredEnabled {
				flags = append(flags, "enable on restart")
			} else {
				flags = append(flags, "disable on restart")
			}
		}
		if p.Entitlement != nil && *p.Entitlement != "" {
			flags = append(flags, "entitlement="+*p.Entitlement)
		}
		line := fmt.Sprintf("%-32s  %-9s  %s", p.ID, p.Status, strings.Join(flags, ", "))
		fmt.Fprintln(out, strings.TrimRight(line, " "))
		if p.Status == "failed" && p.Error != nil {
			fmt.Fprintf(out, "%-32s  error: %s\n", "", *p.Error)
		}
	}
	if !resp.Editable {
		fmt.Fprintln(out, "\nBuilt-in plugins on this deployment are managed by its operator.")
	} else if pending {
		fmt.Fprintln(out, "\nRecorded changes take effect after `valuz restart`.")
	}
}

func newBuiltinPluginToggleCmd(verb string, enabled bool) *cobra.Command {
	return &cobra.Command{
		Use:   verb + " <id>",
		Short: strings.ToUpper(verb[:1]) + verb[1:] + " a built-in backend plugin (applied at the next start)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			client, err := pluginClient(cmd)
			if err != nil {
				return err
			}
			var resp struct {
				Application string `json:"application"`
			}
			path := "/v1/builtin-plugins/" + url.PathEscape(args[0]) + "/enabled"
			if err := client.Post(cmd.Context(), path, map[string]bool{"enabled": enabled}, &resp); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "%s: %sd (%s)\n", args[0], verb, resp.Application)
			return nil
		},
	}
}

// ── dsh plugins ─────────────────────────────────────────────────────────────

func newDshPluginCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "dsh",
		Short: "Manage dsh plugins in Valuz's managed dsh profile (dsh's own PluginManager)",
	}
	cmd.AddCommand(
		newDshPluginStatusCmd(),
		newDshPluginListCmd(),
		newDshPluginInspectCmd(),
		newDshPluginAddCmd(),
		newDshPluginRemoveCmd(),
		newDshPluginToggleCmd("enable", true),
		newDshPluginToggleCmd("disable", false),
		newDshPluginOpenCmd(),
	)
	return cmd
}

// dshRemote calls one pluginManager method and returns dsh's own value.
func dshRemote(ctx context.Context, client *backend.ControlClient, method string, args map[string]any) (any, error) {
	if args == nil {
		args = map[string]any{}
	}
	var resp struct {
		Value any `json:"value"`
	}
	if err := client.Post(ctx, "/v1/dsh/plugins/remote/"+method, map[string]any{"args": args}, &resp); err != nil {
		return nil, err
	}
	return resp.Value, nil
}

func newDshPluginStatusCmd() *cobra.Command {
	var output string
	cmd := &cobra.Command{
		Use:   "status",
		Short: "Show whether the dsh plugin manager can run here and where its profile lives",
		RunE: func(cmd *cobra.Command, _ []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			client, err := pluginClient(cmd)
			if err != nil {
				return err
			}
			var st map[string]any
			if err := client.Get(cmd.Context(), "/v1/dsh/plugins/status", &st); err != nil {
				return err
			}
			if printJSONOutput(cmd.OutOrStdout(), output, st) {
				return nil
			}
			out := cmd.OutOrStdout()
			fmt.Fprintf(out, "available: %v\nrunning:   %v\nprofile:   %v\nhome:      %v\n",
				st["available"], st["running"], st["profile"], st["home"])
			if reason, ok := st["unavailable_reason"].(string); ok && reason != "" {
				fmt.Fprintf(out, "reason:    %s\n", reason)
			}
			return nil
		},
	}
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

func newDshPluginListCmd() *cobra.Command {
	var output string
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List the bundles of the managed dsh profile",
		RunE: func(cmd *cobra.Command, _ []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			client, err := pluginClient(cmd)
			if err != nil {
				return err
			}
			value, err := dshRemote(cmd.Context(), client, "listBundles", nil)
			if err != nil {
				return err
			}
			if printJSONOutput(cmd.OutOrStdout(), output, value) {
				return nil
			}
			var st struct {
				ManagedBundles []string `json:"managed_bundles"`
			}
			if err := client.Get(cmd.Context(), "/v1/dsh/plugins/status", &st); err != nil {
				return err
			}
			managed := map[string]bool{}
			for _, name := range st.ManagedBundles {
				managed[name] = true
			}
			bundles, _ := value.([]any)
			if len(bundles) == 0 {
				fmt.Fprintln(cmd.OutOrStdout(), "(no bundles)")
				return nil
			}
			for _, raw := range bundles {
				b, _ := raw.(map[string]any)
				state := "disabled"
				if enabled, _ := b["enabled"].(bool); enabled {
					state = "enabled"
				}
				notes := []string{}
				name, _ := b["name"].(string)
				if managed[name] {
					notes = append(notes, "managed by Valuz")
				} else if removable, _ := b["removable"].(bool); !removable {
					notes = append(notes, "ships with dsh")
				}
				if e, ok := b["error"].(map[string]any); ok {
					notes = append(notes, fmt.Sprintf("error=%v", e["code"]))
				}
				line := fmt.Sprintf("%-50s  %-14v  %-8s  %s",
					name, strOr(b["version"], "-"), state, strings.Join(notes, ", "))
				fmt.Fprintln(cmd.OutOrStdout(), strings.TrimRight(line, " "))
			}
			return nil
		},
	}
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

func newDshPluginInspectCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "inspect <spec>",
		Short: "Show what an install spec names (npm name, path, tarball or git) without installing",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			client, err := pluginClient(cmd)
			if err != nil {
				return err
			}
			value, err := dshRemote(cmd.Context(), client, "inspect", map[string]any{"spec": args[0]})
			if err != nil {
				return err
			}
			printJSONOutput(cmd.OutOrStdout(), "json", value)
			return nil
		},
	}
}

func newDshPluginAddCmd() *cobra.Command {
	var yes bool
	cmd := &cobra.Command{
		Use:   "add <spec>",
		Short: "Install a dsh bundle the dsh way (inspect, then dsh's pnpm install)",
		Long: "Install a dsh bundle into Valuz's managed dsh profile through dsh's own\n" +
			"PluginManager. dsh plugins are unsigned third-party code that runs with\n" +
			"your privileges: the spec is inspected first and installed only with --yes.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			client, err := pluginClient(cmd)
			if err != nil {
				return err
			}
			spec := args[0]
			inspection, err := dshRemote(cmd.Context(), client, "inspect", map[string]any{"spec": spec})
			if err != nil {
				return err
			}
			out := cmd.OutOrStdout()
			insp, _ := inspection.(map[string]any)
			if insp["status"] != "accepted" {
				printJSONOutput(out, "json", inspection)
				return errs.New(errs.KindUsage, "dsh refused spec %q", spec)
			}
			fmt.Fprintf(out, "%v %v (%v)\n", strOr(insp["name"], spec), strOr(insp["version"], ""), insp["kind"])
			if d, ok := insp["description"].(string); ok && d != "" {
				fmt.Fprintln(out, d)
			}
			if !yes {
				fmt.Fprintln(out, "\nThis runs third-party code with your privileges. Re-run with --yes to install.")
				return nil
			}
			value, err := dshRemote(cmd.Context(), client, "installBundle",
				map[string]any{"spec": spec, "options": map[string]any{"enabled": true}})
			if err != nil {
				return err
			}
			return printDshChange(out, value)
		},
	}
	cmd.Flags().BoolVarP(&yes, "yes", "y", false, "install after inspection without stopping")
	return cmd
}

func newDshPluginRemoveCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "remove <bundle>",
		Short: "Remove a bundle from the managed dsh profile",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			client, err := pluginClient(cmd)
			if err != nil {
				return err
			}
			value, err := dshRemote(cmd.Context(), client, "removeBundle", map[string]any{"name": args[0]})
			if err != nil {
				return err
			}
			return printDshChange(cmd.OutOrStdout(), value)
		},
	}
}

func newDshPluginToggleCmd(verb string, enabled bool) *cobra.Command {
	return &cobra.Command{
		Use:   verb + " <bundle>",
		Short: strings.ToUpper(verb[:1]) + verb[1:] + " a bundle of the managed dsh profile",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			client, err := pluginClient(cmd)
			if err != nil {
				return err
			}
			value, err := dshRemote(cmd.Context(), client, "setBundleEnabled",
				map[string]any{"name": args[0], "enabled": enabled})
			if err != nil {
				return err
			}
			return printDshChange(cmd.OutOrStdout(), value)
		},
	}
}

func newDshPluginOpenCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "open",
		Short: "Start the dsh plugin manager and print its native web UI URL",
		RunE: func(cmd *cobra.Command, _ []string) error {
			client, err := pluginClient(cmd)
			if err != nil {
				return err
			}
			var resp map[string]any
			if err := client.Post(cmd.Context(), "/v1/dsh/plugins/manager/start", map[string]any{}, &resp); err != nil {
				return err
			}
			fmt.Fprintln(cmd.OutOrStdout(), resp["ui_url"])
			return nil
		},
	}
}

// printDshChange renders dsh's ChangeResult; a failed change is an error exit.
func printDshChange(out io.Writer, value any) error {
	change, _ := value.(map[string]any)
	if change == nil {
		printJSONOutput(out, "json", value)
		return nil
	}
	target := strOr(change["bundle"], strOr(change["target"], ""))
	fmt.Fprintf(out, "%s %s: %v\n", strOr(change["stage"], "change"), target, change["application"])
	if warnings, ok := change["warnings"].([]any); ok {
		for _, w := range warnings {
			fmt.Fprintf(out, "warning: %v\n", w)
		}
	}
	if pending, ok := change["pendingBuilds"].([]any); ok && len(pending) > 0 {
		names := make([]string, 0, len(pending))
		for _, p := range pending {
			names = append(names, fmt.Sprint(p))
		}
		sort.Strings(names)
		fmt.Fprintf(out, "build scripts awaiting approval (approve in `valuz plugin dsh open`): %s\n",
			strings.Join(names, ", "))
	}
	if change["application"] == "failed" {
		detail := ""
		if e, ok := change["error"].(map[string]any); ok {
			detail = fmt.Sprintf("%v %v", e["code"], strOr(e["diagnostic"], ""))
		}
		// The operation ran and failed — exit 3, like a failed run.
		return errs.New(errs.KindAgent, "dsh %s failed: %s", strOr(change["stage"], "change"), strings.TrimSpace(detail))
	}
	return nil
}

func strOr(v any, fallback string) string {
	if s, ok := v.(string); ok && s != "" {
		return s
	}
	return fallback
}

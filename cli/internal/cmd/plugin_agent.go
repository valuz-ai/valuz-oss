package cmd

import (
	"encoding/json"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"text/tabwriter"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
)

const (
	agentPluginAPI = "/v1/plugins"
	// Match MAX_ARCHIVE_TOTAL_BYTES and the source download limit in the
	// Agent Plugin service (application plugins have a separate 25 MiB cap).
	agentPluginMaxZipBytes int64 = 50 << 20
)

// newAgentPluginCmd manages skill/connector bundles through the existing
// Agent Plugin library API. These are distinct from application plugins.
func newAgentPluginCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "agent",
		Short: "Manage Agent Plugins (skill and connector bundles)",
		Long: "Agent Plugins bundle skills and MCP connectors (plugin.json).\n\n" +
			"Directory sources are paths on the backend machine. ZIP sources are uploaded\n" +
			"from this computer and work with a remote backend. URL sources are fetched\n" +
			"by the backend. Choose exactly one source or --market-item.",
	}
	cmd.AddCommand(newAgentPluginListCmd(), newAgentPluginShowCmd(),
		newAgentPluginSourceCmd(false), newAgentPluginSourceCmd(true),
		newAgentPluginUpdateCmd(), newAgentPluginToggleCmd("enable"),
		newAgentPluginToggleCmd("disable"), newAgentPluginUninstallCmd(), newAgentPluginExportCmd())
	return cmd
}

func agentPluginPath(id, suffix string) string {
	return agentPluginAPI + "/" + url.PathEscape(id) + suffix
}

// The fields below mirror PluginView, PluginPreview and PluginInstallResult.
// Raw responses are retained for JSON output, including optional server fields.
type agentPluginMember struct {
	Kind           string `json:"kind"`
	Slug           string `json:"slug"`
	Name           string `json:"name"`
	Installed      bool   `json:"installed"`
	ContentDiffers bool   `json:"content_differs"`
}

type agentPluginView struct {
	ID             string              `json:"id"`
	Name           string              `json:"name"`
	Version        string              `json:"version"`
	Description    string              `json:"description"`
	Source         string              `json:"source"`
	SourceRef      string              `json:"source_ref"`
	Composition    string              `json:"composition"`
	Enabled        bool                `json:"enabled"`
	Deletable      bool                `json:"deletable"`
	Protected      bool                `json:"protected"`
	SkillCount     int                 `json:"skill_count"`
	ConnectorCount int                 `json:"connector_count"`
	Members        []agentPluginMember `json:"members"`
}

type agentPluginConflict struct {
	Kind string `json:"kind"`
	Slug string `json:"slug"`
}

type agentPluginSkipped struct {
	Kind   string `json:"kind"`
	Slug   string `json:"slug"`
	Reason string `json:"reason"`
}

type agentPluginPreview struct {
	Manifest    map[string]any        `json:"manifest"`
	Format      string                `json:"format"`
	Composition string                `json:"composition"`
	Members     []agentPluginMember   `json:"members"`
	Conflicts   []agentPluginConflict `json:"conflicts"`
	Skipped     []agentPluginSkipped  `json:"skipped"`
	Warnings    []string              `json:"warnings"`
	Existing    string                `json:"existing"`
}

type agentPluginInstallResult struct {
	Plugin    agentPluginView       `json:"plugin"`
	Status    string                `json:"status"`
	Conflicts []agentPluginConflict `json:"conflicts"`
	Skipped   []agentPluginSkipped  `json:"skipped"`
	Warnings  []string              `json:"warnings"`
}

func newAgentPluginListCmd() *cobra.Command {
	var output string
	cmd := &cobra.Command{
		Use: "list", Short: "List installed Agent Plugins", Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			client, err := pluginClient(cmd)
			if err != nil {
				return err
			}
			var raw json.RawMessage
			if err := client.Get(cmd.Context(), agentPluginAPI, &raw); err != nil {
				return err
			}
			if printJSONOutput(cmd.OutOrStdout(), output, raw) {
				return nil
			}
			var list struct {
				Items []agentPluginView `json:"items"`
			}
			if err := json.Unmarshal(raw, &list); err != nil {
				return errs.Wrap(errs.KindInternal, err, "decode Agent Plugin list")
			}
			if len(list.Items) == 0 {
				fmt.Fprintln(cmd.OutOrStdout(), "(no Agent Plugins installed)")
				return nil
			}
			tw := tabwriter.NewWriter(cmd.OutOrStdout(), 0, 0, 2, ' ', 0)
			fmt.Fprintln(tw, "ID\tNAME\tVERSION\tENABLED\tSKILLS\tCONNECTORS\tSOURCE")
			for _, p := range list.Items {
				fmt.Fprintf(tw, "%s\t%s\t%s\t%t\t%d\t%d\t%s\n", p.ID, p.Name, orDash(p.Version), p.Enabled, p.SkillCount, p.ConnectorCount, p.Source)
			}
			return tw.Flush()
		},
	}
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

func newAgentPluginShowCmd() *cobra.Command {
	var output string
	cmd := &cobra.Command{
		Use: "show <id>", Short: "Show an Agent Plugin and its members", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			client, err := pluginClient(cmd)
			if err != nil {
				return err
			}
			var raw json.RawMessage
			if err := client.Get(cmd.Context(), agentPluginPath(args[0], ""), &raw); err != nil {
				return err
			}
			if printJSONOutput(cmd.OutOrStdout(), output, raw) {
				return nil
			}
			var p agentPluginView
			if err := json.Unmarshal(raw, &p); err != nil {
				return errs.Wrap(errs.KindInternal, err, "decode Agent Plugin")
			}
			printAgentPlugin(cmd.OutOrStdout(), p)
			return nil
		},
	}
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

func printAgentPlugin(out io.Writer, p agentPluginView) {
	fmt.Fprintf(out, "%s %s (%s)\n", p.Name, orDash(p.Version), p.ID)
	if p.Description != "" {
		fmt.Fprintln(out, p.Description)
	}
	fmt.Fprintf(out, "enabled: %t; source: %s; skills: %d; connectors: %d\n", p.Enabled, p.Source, p.SkillCount, p.ConnectorCount)
	if p.SourceRef != "" {
		fmt.Fprintf(out, "source reference: %s\n", p.SourceRef)
	}
	if !p.Deletable {
		fmt.Fprintln(out, "app-managed: disable this plugin to stop using it")
	}
	if p.Protected {
		fmt.Fprintln(out, "protected: export is unavailable")
	}
	for _, m := range p.Members {
		state := ""
		if m.ContentDiffers {
			state = " (library content differs)"
		}
		fmt.Fprintf(out, "  %s %s%s\n", m.Kind, m.Slug, state)
	}
}

// A directory path is interpreted by the backend. A local ZIP is read once so
// preview and installation upload identical bytes even if the file changes.
type agentPluginSource struct {
	Body map[string]string
	File *backend.MultipartFile
}

func readAgentPluginSource(args []string, marketItem, policy string) (*agentPluginSource, error) {
	if policy != "skip" && policy != "overwrite" {
		return nil, errs.New(errs.KindUsage, "--on-conflict must be skip or overwrite")
	}
	if marketItem != "" {
		if len(args) != 0 {
			return nil, errs.New(errs.KindUsage, "choose a source or --market-item, not both")
		}
		if strings.TrimSpace(marketItem) == "" {
			return nil, errs.New(errs.KindUsage, "--market-item must not be empty")
		}
		return &agentPluginSource{Body: map[string]string{"market_item_id": marketItem, "on_conflict": policy}}, nil
	}
	if len(args) != 1 || strings.TrimSpace(args[0]) == "" {
		return nil, errs.New(errs.KindUsage, "provide a directory, ZIP, HTTP(S) URL or --market-item")
	}
	spec := args[0]
	if isURLSpec(spec) {
		u, err := url.Parse(spec)
		if err != nil || u.Hostname() == "" {
			return nil, errs.New(errs.KindUsage, "source must be a valid HTTP(S) URL")
		}
		return &agentPluginSource{Body: map[string]string{"url": spec, "on_conflict": policy}}, nil
	}
	if !strings.EqualFold(filepath.Ext(spec), ".zip") {
		abs, err := filepath.Abs(spec)
		if err != nil {
			return nil, errs.Wrap(errs.KindUsage, err, "resolve directory path")
		}
		return &agentPluginSource{Body: map[string]string{"path": abs, "on_conflict": policy}}, nil
	}
	f, err := os.Open(spec)
	if err != nil {
		return nil, errs.Wrap(errs.KindUsage, err, "open ZIP %s", spec)
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return nil, errs.Wrap(errs.KindUsage, err, "stat ZIP %s", spec)
	}
	if !info.Mode().IsRegular() {
		return nil, errs.New(errs.KindUsage, "ZIP source must be a regular file")
	}
	if info.Size() > agentPluginMaxZipBytes {
		return nil, errs.New(errs.KindUsage, "ZIP exceeds the 50 MiB package limit")
	}
	data, err := io.ReadAll(io.LimitReader(f, agentPluginMaxZipBytes+1))
	if err != nil {
		return nil, errs.Wrap(errs.KindUsage, err, "read ZIP %s", spec)
	}
	if int64(len(data)) > agentPluginMaxZipBytes {
		return nil, errs.New(errs.KindUsage, "ZIP exceeds the 50 MiB package limit")
	}
	return &agentPluginSource{Body: map[string]string{"on_conflict": policy}, File: &backend.MultipartFile{Field: "file", FileName: filepath.Base(spec), ContentType: "application/zip", Data: data}}, nil
}

func (s *agentPluginSource) post(cmd *cobra.Command, client *backend.ControlClient, suffix string, out any) error {
	if s.File != nil {
		return client.PostMultipart(cmd.Context(), agentPluginAPI+suffix, []backend.MultipartField{{Name: "on_conflict", Value: s.Body["on_conflict"]}}, *s.File, out)
	}
	return client.Post(cmd.Context(), agentPluginAPI+suffix, s.Body, out)
}

func newAgentPluginSourceCmd(install bool) *cobra.Command {
	var output, marketItem, policy string
	var yes bool
	verb := "preview"
	if install {
		verb = "install"
	}
	cmd := &cobra.Command{
		Use: verb + " [dir|zip|http(s)-url]", Short: strings.Title(verb) + " an Agent Plugin from a source",
		Long: strings.Title(verb) + " an Agent Plugin. Provide exactly one directory, ZIP, URL or --market-item.\n" +
			"Directories are paths on the backend machine; relative paths are resolved\n" +
			"against this CLI's working directory. ZIP files are uploaded from this computer.\n" +
			"Conflicting library members are skipped by default; overwrite replaces them.",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			if cmd.Flags().Changed("market-item") && strings.TrimSpace(marketItem) == "" {
				return errs.New(errs.KindUsage, "--market-item must not be empty")
			}
			source, err := readAgentPluginSource(args, marketItem, policy)
			if err != nil {
				return err
			}
			client, err := pluginClient(cmd)
			if err != nil {
				return err
			}
			var raw json.RawMessage
			if err := source.post(cmd, client, "/preview", &raw); err != nil {
				return err
			}
			if !install && printJSONOutput(cmd.OutOrStdout(), output, raw) {
				return nil
			}
			var preview agentPluginPreview
			if err := json.Unmarshal(raw, &preview); err != nil {
				return errs.Wrap(errs.KindInternal, err, "decode Agent Plugin preview")
			}
			summaryOut := cmd.OutOrStdout()
			if output == "json" {
				summaryOut = cmd.ErrOrStderr()
			}
			printAgentPluginPreview(summaryOut, preview, policy)
			if !install {
				return nil
			}
			if preview.Existing == "other_source" {
				return errs.New(errs.KindUsage, "a same-name Agent Plugin is installed from another source; nothing was installed")
			}
			if !yes {
				ok, err := confirm(cmd, summaryOut, "Install these skills and connectors? [y/N] ")
				if err != nil {
					return err
				}
				if !ok {
					return errs.New(errs.KindUsage, "installation cancelled; nothing was installed (use --yes to confirm non-interactively)")
				}
			}
			if err := source.post(cmd, client, "/install", &raw); err != nil {
				return err
			}
			return renderAgentPluginInstall(cmd.OutOrStdout(), output, raw)
		},
	}
	cmd.Flags().StringVar(&marketItem, "market-item", "", "install/preview a marketplace item ID instead of a source argument")
	cmd.Flags().StringVar(&policy, "on-conflict", "skip", "library member conflicts: skip|overwrite")
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	if install {
		cmd.Flags().BoolVarP(&yes, "yes", "y", false, "install without asking for confirmation")
	}
	return cmd
}

func printAgentPluginPreview(out io.Writer, p agentPluginPreview, policy string) {
	fmt.Fprintf(out, "Agent Plugin: %v; format: %s; composition: %s\n", p.Manifest["name"], p.Format, p.Composition)
	for _, m := range p.Members {
		fmt.Fprintf(out, "  %s %s\n", m.Kind, m.Slug)
	}
	if p.Existing != "" {
		fmt.Fprintf(out, "existing plugin: %s\n", p.Existing)
	}
	printAgentPluginFindings(out, p.Conflicts, p.Skipped, p.Warnings)
	if len(p.Conflicts) > 0 {
		fmt.Fprintf(out, "conflict policy: %s\n", policy)
	}
}

func printAgentPluginFindings(out io.Writer, conflicts []agentPluginConflict, skipped []agentPluginSkipped, warnings []string) {
	for _, c := range conflicts {
		fmt.Fprintf(out, "conflict: %s %s\n", c.Kind, c.Slug)
	}
	for _, s := range skipped {
		fmt.Fprintf(out, "skipped: %s %s (%s)\n", s.Kind, s.Slug, s.Reason)
	}
	for _, w := range warnings {
		fmt.Fprintf(out, "warning: %s\n", w)
	}
}

func renderAgentPluginInstall(out io.Writer, output string, raw json.RawMessage) error {
	if printJSONOutput(out, output, raw) {
		return nil
	}
	var result agentPluginInstallResult
	if err := json.Unmarshal(raw, &result); err != nil {
		return errs.Wrap(errs.KindInternal, err, "decode Agent Plugin installation result")
	}
	fmt.Fprintf(out, "%s %s %s (%s)\n", result.Status, result.Plugin.Name, orDash(result.Plugin.Version), result.Plugin.ID)
	printAgentPluginFindings(out, result.Conflicts, result.Skipped, result.Warnings)
	return nil
}

func newAgentPluginUpdateCmd() *cobra.Command {
	var output, policy string
	var yes bool
	cmd := &cobra.Command{
		Use: "update <id>", Short: "Update an Agent Plugin from its saved source", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			if policy != "skip" && policy != "overwrite" {
				return errs.New(errs.KindUsage, "--on-conflict must be skip or overwrite")
			}
			client, err := pluginClient(cmd)
			if err != nil {
				return err
			}
			if !yes {
				out := cmd.OutOrStdout()
				if output == "json" {
					out = cmd.ErrOrStderr()
				}
				fmt.Fprintf(out, "Re-read the saved source for Agent Plugin %s (conflict policy: %s).\n", args[0], policy)
				ok, err := confirm(cmd, out, "Update this Agent Plugin? [y/N] ")
				if err != nil {
					return err
				}
				if !ok {
					return errs.New(errs.KindUsage, "update cancelled; nothing was changed (use --yes to confirm non-interactively)")
				}
			}
			var raw json.RawMessage
			if err := client.Post(cmd.Context(), agentPluginPath(args[0], "/update"), map[string]string{"on_conflict": policy}, &raw); err != nil {
				return err
			}
			return renderAgentPluginInstall(cmd.OutOrStdout(), output, raw)
		},
	}
	cmd.Flags().StringVar(&policy, "on-conflict", "skip", "library member conflicts: skip|overwrite")
	cmd.Flags().BoolVarP(&yes, "yes", "y", false, "update without asking for confirmation")
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

func newAgentPluginToggleCmd(verb string) *cobra.Command {
	var output string
	cmd := &cobra.Command{
		Use: verb + " <id>", Short: strings.Title(verb) + " an Agent Plugin and its members", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			client, err := pluginClient(cmd)
			if err != nil {
				return err
			}
			var raw json.RawMessage
			if err := client.Post(cmd.Context(), agentPluginPath(args[0], "/"+verb), nil, &raw); err != nil {
				return err
			}
			if !printJSONOutput(cmd.OutOrStdout(), output, raw) {
				fmt.Fprintf(cmd.OutOrStdout(), "%sd Agent Plugin %s\n", verb, args[0])
			}
			return nil
		},
	}
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

func newAgentPluginUninstallCmd() *cobra.Command {
	var output string
	var yes bool
	cmd := &cobra.Command{
		Use: "uninstall <id>", Short: "Uninstall an Agent Plugin, retaining shared members", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			client, err := pluginClient(cmd)
			if err != nil {
				return err
			}
			if !yes {
				out := cmd.OutOrStdout()
				if output == "json" {
					out = cmd.ErrOrStderr()
				}
				fmt.Fprintf(out, "Uninstall Agent Plugin %s; members used by other plugins or installed separately are kept.\n", args[0])
				ok, err := confirm(cmd, out, "Uninstall this Agent Plugin? [y/N] ")
				if err != nil {
					return err
				}
				if !ok {
					return errs.New(errs.KindUsage, "uninstall cancelled; nothing was removed (use --yes to confirm non-interactively)")
				}
			}
			var raw json.RawMessage
			if err := client.Delete(cmd.Context(), agentPluginPath(args[0], ""), &raw); err != nil {
				return err
			}
			if printJSONOutput(cmd.OutOrStdout(), output, raw) {
				return nil
			}
			var result struct {
				Removed []agentPluginConflict `json:"removed_members"`
				Kept    []agentPluginSkipped  `json:"kept_members"`
			}
			if err := json.Unmarshal(raw, &result); err != nil {
				return errs.Wrap(errs.KindInternal, err, "decode Agent Plugin uninstall result")
			}
			fmt.Fprintf(cmd.OutOrStdout(), "uninstalled Agent Plugin %s\n", args[0])
			for _, m := range result.Removed {
				fmt.Fprintf(cmd.OutOrStdout(), "removed: %s %s\n", m.Kind, m.Slug)
			}
			for _, m := range result.Kept {
				fmt.Fprintf(cmd.OutOrStdout(), "kept: %s %s (%s)\n", m.Kind, m.Slug, m.Reason)
			}
			return nil
		},
	}
	cmd.Flags().BoolVarP(&yes, "yes", "y", false, "uninstall without asking for confirmation")
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

func newAgentPluginExportCmd() *cobra.Command {
	var output string
	var force bool
	cmd := &cobra.Command{
		Use: "export <id> <zip>", Short: "Export an Agent Plugin to a local ZIP", Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			path, err := filepath.Abs(args[1])
			if err != nil {
				return errs.Wrap(errs.KindUsage, err, "resolve ZIP destination")
			}
			if !force {
				if _, err := os.Lstat(path); err == nil {
					return errs.New(errs.KindUsage, "%s already exists (use --force to replace it)", path)
				} else if !os.IsNotExist(err) {
					return errs.Wrap(errs.KindUsage, err, "check ZIP destination")
				}
			}
			client, err := pluginClient(cmd)
			if err != nil {
				return err
			}
			f, err := os.CreateTemp(filepath.Dir(path), ".valuz-agent-plugin-*.zip")
			if err != nil {
				return errs.Wrap(errs.KindUsage, err, "create ZIP destination")
			}
			defer os.Remove(f.Name())
			defer f.Close()
			n, err := client.Download(cmd.Context(), agentPluginPath(args[0], "/export"), f, agentPluginMaxZipBytes)
			if err != nil {
				return err
			}
			if err := f.Sync(); err != nil {
				return errs.Wrap(errs.KindInternal, err, "flush exported ZIP")
			}
			if err := f.Close(); err != nil {
				return errs.Wrap(errs.KindInternal, err, "close exported ZIP")
			}
			if force {
				err = os.Rename(f.Name(), path)
			} else {
				// A link publishes the complete file atomically and refuses to
				// replace a destination created after the initial existence check.
				err = os.Link(f.Name(), path)
			}
			if err != nil {
				return errs.Wrap(errs.KindUsage, err, "save exported ZIP (use --force to replace an existing file)")
			}
			if !printJSONOutput(cmd.OutOrStdout(), output, map[string]any{"path": path, "size": n}) {
				fmt.Fprintf(cmd.OutOrStdout(), "exported %s (%d bytes)\n", path, n)
			}
			return nil
		},
	}
	cmd.Flags().BoolVar(&force, "force", false, "replace an existing destination file")
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

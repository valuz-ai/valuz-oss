package cmd

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/spf13/cobra"

	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/pluginpkg"
)

// newPluginCmd is the single public entry point for all plugin families.
// Manifest formats and HTTP endpoints belong to each family; they are not
// interchangeable even though lifecycle verbs share the same names.
func newPluginCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "plugin",
		Short: "Manage agent, app, built-in and DSH plugins",
		Long: "Choose the plugin family:\n\n" +
			"  agent    skills and MCP connectors (Agent Plugins)\n" +
			"  app      application pages, panels and automations (valuz-plugin.json)\n" +
			"  builtin  built-in backend plugins (changes may need a restart)\n" +
			"  dsh      native plugins of the managed DSH profile",
	}
	cmd.AddCommand(newAgentPluginCmd(), newAppPluginCmd(), newBuiltinPluginCmd(), newExtDshCmd())
	// The unqualified verbs previously managed app plugins. Preserve scripts
	// while keeping the help/completion surface unambiguous.
	for _, legacy := range newAppPluginCommands() {
		legacy.Hidden = true
		warnLegacyPluginCommand(legacy, "valuz plugin app "+legacy.Name())
		cmd.AddCommand(legacy)
	}
	return cmd
}

// warnLegacyPluginCommand writes compatibility notices to stderr. Cobra's
// Deprecated field writes to stdout, which would break `-o json` consumers.
func warnLegacyPluginCommand(cmd *cobra.Command, replacement string) {
	if run := cmd.RunE; run != nil {
		cmd.RunE = func(cmd *cobra.Command, args []string) error {
			fmt.Fprintf(cmd.ErrOrStderr(), "Deprecated: use `%s` instead of `%s`.\n", replacement, cmd.CommandPath())
			return run(cmd, args)
		}
	}
}

// newAppPluginCmd builds `valuz plugin app ...` — application plugins
// (docs/design/plugin-architecture/plugin-development 03/04/09, task card
// 04 §D/§F). Three groups:
//
//   - local, no backend: validate, pack (internal/pluginpkg, the same rules
//     and file set as the SDK CLI and the backend);
//   - the local Valuz backend (/v1/extensions/third-party…): dev, install,
//     list, status, logs, enable, disable, reload, uninstall;
//   - the control plane (<cloud>/v1/extensions/submissions): publish,
//     submissions — no local Valuz needed.
func newAppPluginCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "app",
		Short: "Develop, install and publish application plugins",
		Long: "Application plugins (valuz-plugin.json, plugin API " + pluginpkg.PluginAPIVersion + ").\n\n" +
			"  validate, pack           check and zip a plugin directory (offline)\n" +
			"  dev, install, list, …    manage the plugins of the local Valuz\n" +
			"  publish, submissions     publish to your personal / org / global catalog",
	}
	cmd.AddCommand(newAppPluginCommands()...)
	return cmd
}

func newAppPluginCommands() []*cobra.Command {
	return []*cobra.Command{
		newPluginValidateCmd(),
		newPluginPackCmd(),
		newPluginDevCmd(),
		newPluginInstallCmd(),
		newPluginListCmd(),
		newPluginStatusCmd(),
		newPluginLogsCmd(),
		newPluginToggleCmd("enable"),
		newPluginToggleCmd("disable"),
		newPluginReloadCmd(),
		newPluginUninstallCmd(),
		newPluginPublishCmd(),
		newPluginSubmissionsCmd(),
	}
}

const (
	thirdPartyAPI   = "/v1/extensions/third-party"
	submissionsAPI  = "/v1/extensions/submissions"
	catalogAPI      = "/v1/extensions/catalog"
	installQuestion = "Run this third-party code on this computer with your permissions? [y/N] "
)

// pluginLogsPollInterval is the `logs -f` polling period (tests shorten it).
var pluginLogsPollInterval = time.Second

func pluginPath(id, suffix string) string {
	return thirdPartyAPI + "/" + url.PathEscape(id) + suffix
}

// pluginItem is the backend's Item (task card 04 §D). Shapes the CLI only
// renders stay generic so a richer server value never breaks decoding.
type pluginItem struct {
	ID            string   `json:"id"`
	Version       string   `json:"version"`
	Name          any      `json:"name"`
	Description   any      `json:"description"`
	Publisher     any      `json:"publisher"`
	Source        any      `json:"source"`
	Status        string   `json:"status"`
	StatusReason  string   `json:"status_reason"`
	Enabled       bool     `json:"enabled"`
	Permissions   []string `json:"permissions"`
	Requires      []string `json:"requires"`
	UnmetRequires []string `json:"unmet_requires"`
	EntryURL      string   `json:"entry_url"`
	StyleURLs     []string `json:"style_urls"`
	Automations   []any    `json:"automations"`
	Revision      any      `json:"revision"`
	DevPath       string   `json:"dev_path"`
	SHA256        string   `json:"sha256"`
	InstalledAt   string   `json:"installed_at"`
}

type pluginList struct {
	APIVersion     string            `json:"api_version"`
	SafeMode       bool              `json:"safe_mode"`
	SafeModeReason string            `json:"safe_mode_reason"`
	Generation     any               `json:"generation"`
	Plugins        []json.RawMessage `json:"plugins"`
}

type pluginEnvelope struct {
	Plugin      pluginItem `json:"plugin"`
	UpdatedFrom string     `json:"updated_from"`
}

type inspectResult struct {
	Manifest         map[string]any `json:"manifest"`
	SHA256           string         `json:"sha256"`
	Size             int64          `json:"size"`
	Permissions      []string       `json:"permissions"`
	Requires         []string       `json:"requires"`
	UnmetRequires    []string       `json:"unmet_requires"`
	Errors           []any          `json:"errors"`
	Warnings         []any          `json:"warnings"`
	HasBackend       bool           `json:"has_backend"`
	AddedPermissions []string       `json:"added_permissions"`
	Existing         *struct {
		Version string `json:"version"`
	} `json:"existing"`
}

// ── validate / pack (offline) ───────────────────────────────────────────────

func newPluginValidateCmd() *cobra.Command {
	var output string
	cmd := &cobra.Command{
		Use:   "validate [dir|zip]",
		Short: "Check a plugin directory (or a packed zip) against the manifest rules",
		Args:  cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			target := "."
			if len(args) == 1 {
				target = args[0]
			}
			res, err := validateTarget(target)
			if err != nil {
				return errs.Wrap(errs.KindUsage, err, "validate %s: %v", target, err)
			}
			out := cmd.OutOrStdout()
			if !printJSONOutput(out, output, res) {
				printValidation(out, res)
			}
			if !res.OK {
				return invalidPluginError(res)
			}
			return nil
		},
	}
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

func validateTarget(target string) (*pluginpkg.Result, error) {
	info, err := os.Stat(target)
	if err != nil {
		return nil, err
	}
	if info.IsDir() {
		return pluginpkg.ValidateDir(target)
	}
	return pluginpkg.ValidateZip(target)
}

func invalidPluginError(res *pluginpkg.Result) error {
	return errs.New(errs.KindUsage, "plugin is invalid: %d error(s)", len(res.Errors))
}

func printValidation(out io.Writer, res *pluginpkg.Result) {
	if res.OK {
		fmt.Fprintf(out, "%s %s: ok\n", orDash(res.ID()), orDash(res.Version()))
	}
	for _, e := range res.Errors {
		fmt.Fprintf(out, "error: %s\n", e)
	}
	for _, w := range res.Warnings {
		fmt.Fprintf(out, "warning: %s\n", w)
	}
}

func newPluginPackCmd() *cobra.Command {
	var (
		output string
		outDir string
	)
	cmd := &cobra.Command{
		Use:   "pack [dir]",
		Short: "Validate and zip a plugin into <id>-<version>.zip (default <dir>/dist)",
		Long: "Validate the plugin and write a deterministic zip (sorted entries, fixed\n" +
			"timestamps): valuz-plugin.json, frontend/, the locales directory,\n" +
			"automations/, the icon, README.md and LICENSE*. node_modules, .git, src\n" +
			"and dot-files are never packed. Prints the path and sha256.",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			dir := "."
			if len(args) == 1 {
				dir = args[0]
			}
			res, err := pluginpkg.Pack(dir, outDir)
			out := cmd.OutOrStdout()
			if err != nil {
				var invalid *pluginpkg.InvalidError
				if errors.As(err, &invalid) {
					if !printJSONOutput(out, output, invalid.Result) {
						printValidation(out, invalid.Result)
					}
					return errs.New(errs.KindUsage, "%s: %d error(s)", invalid.Stage, len(invalid.Result.Errors))
				}
				return errs.Wrap(errs.KindUsage, err, "pack %s: %v", dir, err)
			}
			if printJSONOutput(out, output, res) {
				return nil
			}
			for _, w := range res.Warnings {
				fmt.Fprintf(out, "warning: %s\n", w)
			}
			fmt.Fprintf(out, "packed  %s %s (%d files, %d bytes)\n", res.ID, res.Version, len(res.Files), res.Size)
			fmt.Fprintf(out, "path    %s\n", res.Path)
			fmt.Fprintf(out, "sha256  %s\n", res.SHA256)
			return nil
		},
	}
	cmd.Flags().StringVar(&outDir, "out", "", "output directory (default <dir>/dist)")
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

// ── local backend ───────────────────────────────────────────────────────────

func newPluginDevCmd() *cobra.Command {
	var output string
	cmd := &cobra.Command{
		Use:   "dev [dir]",
		Short: "Link a plugin directory into the local Valuz in development mode",
		Long: "Validate the directory, then link it into the local Valuz without\n" +
			"copying: Valuz loads the built frontend straight from the directory.",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			dir := "."
			if len(args) == 1 {
				dir = args[0]
			}
			abs, err := filepath.Abs(dir)
			if err != nil {
				return errs.Wrap(errs.KindUsage, err, "resolve %s", dir)
			}
			res, err := pluginpkg.ValidateDir(abs)
			if err != nil {
				return errs.Wrap(errs.KindUsage, err, "validate %s: %v", dir, err)
			}
			out := cmd.OutOrStdout()
			if !res.OK {
				if !printJSONOutput(out, output, res) {
					printValidation(out, res)
				}
				return invalidPluginError(res)
			}
			client, err := extClient(cmd)
			if err != nil {
				return err
			}
			var raw json.RawMessage
			if err := client.Post(cmd.Context(), thirdPartyAPI+"/dev-link", map[string]string{"path": abs}, &raw); err != nil {
				return err
			}
			if printJSONOutput(out, output, raw) {
				return nil
			}
			for _, w := range res.Warnings {
				fmt.Fprintf(out, "warning: %s\n", w)
			}
			var env pluginEnvelope
			_ = json.Unmarshal(raw, &env)
			p := env.Plugin
			fmt.Fprintf(out, "linked   %s %s (dev) from %s\n", orDash(p.ID), p.Version, abs)
			fmt.Fprintf(out, "status   %s\n", statusText(p))
			fmt.Fprintf(out, "revision %s\n", revisionText(p.Revision))
			fmt.Fprintf(out, "entry    %s\n", orDash(p.EntryURL))
			return nil
		},
	}
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

func newPluginInstallCmd() *cobra.Command {
	var (
		output string
		yes    bool
	)
	cmd := &cobra.Command{
		Use:   "install <zip|dir|https-url>",
		Short: "Install a plugin into the local Valuz (shows what it is and asks first)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			spec := args[0]
			source := map[string]any{}
			display := spec
			if isURLSpec(spec) {
				source["url"] = spec
			} else {
				abs, err := filepath.Abs(spec)
				if err != nil {
					return errs.Wrap(errs.KindUsage, err, "resolve %s", spec)
				}
				if _, err := os.Stat(abs); err != nil {
					return errs.Wrap(errs.KindUsage, err, "%s: no such file or directory", spec)
				}
				source["source_path"] = abs
				display = abs
			}
			client, err := extClient(cmd)
			if err != nil {
				return err
			}
			var inspRaw json.RawMessage
			if err := client.Post(cmd.Context(), thirdPartyAPI+"/inspect", source, &inspRaw); err != nil {
				return err
			}
			var insp inspectResult
			if err := json.Unmarshal(inspRaw, &insp); err != nil {
				// A field of an unexpected type is skipped (the rest still
				// decodes); anything else is not an inspect reply.
				var typeErr *json.UnmarshalTypeError
				if !errors.As(err, &typeErr) {
					return errs.Wrap(errs.KindInternal, err, "decode inspect response")
				}
			}

			jsonOut := output == "json"
			out := cmd.OutOrStdout()
			// The summary is what the user confirms; in JSON mode it goes to
			// stderr so stdout stays one JSON document.
			summaryOut := out
			if jsonOut {
				summaryOut = cmd.ErrOrStderr()
			}
			if !jsonOut || !yes {
				printPackageSummary(summaryOut, inspectSummary(insp, display))
			}
			if len(insp.Errors) > 0 {
				if jsonOut {
					printJSONOutput(out, output, map[string]json.RawMessage{"inspect": inspRaw})
				}
				return errs.New(errs.KindUsage, "%s cannot be installed: %d error(s)", display, len(insp.Errors))
			}
			if !yes {
				ok, err := confirm(cmd, summaryOut, installQuestion)
				if err != nil {
					return err
				}
				if !ok {
					return errs.New(errs.KindUsage, "installation cancelled; nothing was installed")
				}
			}

			body := map[string]any{}
			for k, v := range source {
				body[k] = v
			}
			if insp.SHA256 != "" {
				// Pin the inspected bytes: a URL or file that changed between
				// inspect and install is rejected (422 sha256_mismatch).
				body["expected_sha256"] = insp.SHA256
			}
			var instRaw json.RawMessage
			if err := client.Post(cmd.Context(), thirdPartyAPI+"/install", body, &instRaw); err != nil {
				return err
			}
			if jsonOut {
				printJSONOutput(out, output, map[string]json.RawMessage{"inspect": inspRaw, "install": instRaw})
				return nil
			}
			var env pluginEnvelope
			_ = json.Unmarshal(instRaw, &env)
			p := env.Plugin
			verb := "installed"
			if env.UpdatedFrom != "" {
				verb = "updated"
			}
			line := fmt.Sprintf("\n%s %s %s", verb, orDash(p.ID), p.Version)
			if env.UpdatedFrom != "" {
				line += " (from " + env.UpdatedFrom + ")"
			}
			fmt.Fprintln(out, line)
			fmt.Fprintf(out, "status: %s\n", statusText(p))
			return nil
		},
	}
	cmd.Flags().BoolVarP(&yes, "yes", "y", false, "install without asking for confirmation")
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

func isURLSpec(s string) bool {
	lower := strings.ToLower(s)
	return strings.HasPrefix(lower, "https://") || strings.HasPrefix(lower, "http://")
}

func newPluginListCmd() *cobra.Command {
	var output string
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List the third-party plugins of the local Valuz",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			list, raw, err := fetchPluginList(cmd)
			if err != nil {
				return err
			}
			out := cmd.OutOrStdout()
			if printJSONOutput(out, output, raw) {
				return nil
			}
			if list.SafeMode {
				reason := ""
				if list.SafeModeReason != "" {
					reason = " (" + list.SafeModeReason + ")"
				}
				fmt.Fprintf(out, "Safe mode is on%s: third-party plugins are not loaded.\n\n", reason)
			}
			if len(list.Plugins) == 0 {
				fmt.Fprintln(out, "(no third-party plugins installed)")
				return nil
			}
			tw := tabwriter.NewWriter(out, 0, 0, 2, ' ', 0)
			fmt.Fprintln(tw, "ID\tVERSION\tSTATUS\tSOURCE\tREVISION")
			for _, rawItem := range list.Plugins {
				var p pluginItem
				_ = json.Unmarshal(rawItem, &p)
				fmt.Fprintf(tw, "%s\t%s\t%s\t%s\t%s\n",
					p.ID, orDash(p.Version), orDash(p.Status), sourceKind(p.Source), revisionText(p.Revision))
			}
			return tw.Flush()
		},
	}
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

func fetchPluginList(cmd *cobra.Command) (*pluginList, json.RawMessage, error) {
	client, err := extClient(cmd)
	if err != nil {
		return nil, nil, err
	}
	var raw json.RawMessage
	if err := client.Get(cmd.Context(), thirdPartyAPI, &raw); err != nil {
		return nil, nil, err
	}
	var list pluginList
	if err := json.Unmarshal(raw, &list); err != nil {
		return nil, nil, errs.Wrap(errs.KindInternal, err, "decode %s response", thirdPartyAPI)
	}
	return &list, raw, nil
}

func newPluginStatusCmd() *cobra.Command {
	var output string
	cmd := &cobra.Command{
		Use:   "status <id>",
		Short: "Show one plugin: status and reason, source, permissions, requirements",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			list, _, err := fetchPluginList(cmd)
			if err != nil {
				return err
			}
			for _, rawItem := range list.Plugins {
				var p pluginItem
				_ = json.Unmarshal(rawItem, &p) // tolerate drift in fields the CLI only renders
				if p.ID != args[0] {
					continue
				}
				out := cmd.OutOrStdout()
				if printJSONOutput(out, output, rawItem) {
					return nil
				}
				printPluginDetail(out, p, list)
				return nil
			}
			return errs.New(errs.KindUsage, "app plugin %q is not installed (see `valuz plugin app list`)", args[0])
		},
	}
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

func printPluginDetail(out io.Writer, p pluginItem, list *pluginList) {
	row := func(k, v string) { fmt.Fprintf(out, "%-15s %s\n", k+":", v) }
	row("id", p.ID)
	if name := pluginpkg.LocalizedText(p.Name); name != "" {
		row("name", name)
	}
	row("version", orDash(p.Version))
	row("publisher", orDash(publisherName(p.Publisher)))
	row("status", orDash(p.Status))
	if p.StatusReason != "" {
		row("status reason", p.StatusReason)
	}
	if list.SafeMode {
		row("safe mode", "on (third-party plugins are not loaded)")
	}
	row("enabled", strconv.FormatBool(p.Enabled))
	row("source", sourceDetail(p.Source))
	if p.DevPath != "" {
		row("dev path", p.DevPath)
	}
	row("revision", revisionText(p.Revision))
	row("permissions", listOrNone(p.Permissions))
	row("requires", listOrNone(p.Requires))
	if len(p.UnmetRequires) > 0 {
		row("unmet requires", strings.Join(p.UnmetRequires, ", "))
	}
	if names := automationNames(p.Automations); len(names) > 0 {
		row("automations", strings.Join(names, ", "))
	}
	row("entry", orDash(p.EntryURL))
	if p.SHA256 != "" {
		row("sha256", p.SHA256)
	}
	if p.InstalledAt != "" {
		row("installed at", p.InstalledAt)
	}
}

type logEntry struct {
	TS      any    `json:"ts"`
	Level   string `json:"level"`
	Message string `json:"message"`
	Source  string `json:"source"`
}

func (e logEntry) key() string {
	return fmt.Sprint(e.TS) + "\x00" + e.Level + "\x00" + e.Source + "\x00" + e.Message
}

func newPluginLogsCmd() *cobra.Command {
	var (
		output string
		lines  int
		follow bool
	)
	cmd := &cobra.Command{
		Use:   "logs <id>",
		Short: "Show a plugin's log (frontend ctx.log, backend and audit entries)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			if lines <= 0 {
				return errs.New(errs.KindUsage, "-n must be positive")
			}
			client, err := extClient(cmd)
			if err != nil {
				return err
			}
			id := args[0]
			out := cmd.OutOrStdout()
			jsonOut := output == "json"
			fetch := func(limit int) ([]logEntry, error) {
				var resp struct {
					Entries []logEntry `json:"entries"`
				}
				err := client.Get(cmd.Context(), pluginPath(id, "/logs")+"?limit="+strconv.Itoa(limit), &resp)
				return resp.Entries, err
			}
			entries, err := fetch(lines)
			if err != nil {
				return err
			}
			if len(entries) > lines {
				entries = entries[len(entries)-lines:]
			}
			if jsonOut && !follow {
				printJSONOutput(out, output, map[string]any{"entries": entries})
				return nil
			}
			emit := func(e logEntry) {
				if jsonOut {
					raw, _ := json.Marshal(e)
					fmt.Fprintln(out, string(raw))
					return
				}
				printLogEntry(out, e)
			}
			last := ""
			for _, e := range entries {
				emit(e)
				last = e.key()
			}
			if !follow {
				return nil
			}
			window := lines
			if window < 200 {
				window = 200
			}
			ticker := time.NewTicker(pluginLogsPollInterval)
			defer ticker.Stop()
			for {
				select {
				case <-cmd.Context().Done():
					return nil
				case <-ticker.C:
				}
				cur, err := fetch(window)
				if err != nil {
					if cmd.Context().Err() != nil {
						return nil
					}
					return err
				}
				for _, e := range newLogEntries(cur, last) {
					emit(e)
					last = e.key()
				}
			}
		},
	}
	cmd.Flags().IntVarP(&lines, "lines", "n", 100, "number of entries to show")
	cmd.Flags().BoolVarP(&follow, "follow", "f", false, "keep polling for new entries (Ctrl+C to stop)")
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json (json lines with -f)")
	return cmd
}

// newLogEntries returns the entries of cur after the last printed one (all
// of cur when it is gone — rotated out of the window).
func newLogEntries(cur []logEntry, lastKey string) []logEntry {
	if lastKey == "" {
		return cur
	}
	for i := len(cur) - 1; i >= 0; i-- {
		if cur[i].key() == lastKey {
			return cur[i+1:]
		}
	}
	return cur
}

func printLogEntry(out io.Writer, e logEntry) {
	src := ""
	if e.Source != "" {
		src = "[" + e.Source + "] "
	}
	fmt.Fprintf(out, "%v %-5s %s%s\n", e.TS, strings.ToUpper(orDash(e.Level)), src, e.Message)
}

func newPluginToggleCmd(verb string) *cobra.Command {
	var output string
	cmd := &cobra.Command{
		Use:   verb + " <id>",
		Short: strings.ToUpper(verb[:1]) + verb[1:] + " a third-party plugin (applies immediately)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return postPluginAction(cmd, output, args[0], "/"+verb, func(out io.Writer, p pluginItem) {
				fmt.Fprintf(out, "%s: %sd (status %s)\n", args[0], verb, statusText(p))
			})
		},
	}
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

func newPluginReloadCmd() *cobra.Command {
	var output string
	cmd := &cobra.Command{
		Use:   "reload <id>",
		Short: "Reload a development-mode plugin (revision + 1)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return postPluginAction(cmd, output, args[0], "/reload", func(out io.Writer, p pluginItem) {
				fmt.Fprintf(out, "%s: reloaded (revision %s, status %s)\n", args[0], revisionText(p.Revision), statusText(p))
			})
		},
	}
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

// postPluginAction POSTs /{id}<suffix> and renders the {"plugin": Item} reply.
func postPluginAction(cmd *cobra.Command, output, id, suffix string, human func(io.Writer, pluginItem)) error {
	if err := checkOutputFormat(output); err != nil {
		return err
	}
	client, err := extClient(cmd)
	if err != nil {
		return err
	}
	var raw json.RawMessage
	if err := client.Post(cmd.Context(), pluginPath(id, suffix), map[string]any{}, &raw); err != nil {
		return err
	}
	if printJSONOutput(cmd.OutOrStdout(), output, raw) {
		return nil
	}
	var env pluginEnvelope
	_ = json.Unmarshal(raw, &env)
	human(cmd.OutOrStdout(), env.Plugin)
	return nil
}

func newPluginUninstallCmd() *cobra.Command {
	var (
		output    string
		yes       bool
		purgeData bool
	)
	cmd := &cobra.Command{
		Use:   "uninstall <id>",
		Short: "Uninstall a plugin and delete the automations it created",
		Long: "Uninstall a plugin and delete the automations it created. Its data\n" +
			"(storage, settings, extensions-data) is kept unless --purge-data.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			id := args[0]
			client, err := extClient(cmd)
			if err != nil {
				return err
			}
			if !yes {
				promptOut := cmd.OutOrStdout()
				if output == "json" {
					promptOut = cmd.ErrOrStderr()
				}
				question := fmt.Sprintf("Uninstall %s? Its data is kept. [y/N] ", id)
				if purgeData {
					question = fmt.Sprintf("Uninstall %s and delete its data? [y/N] ", id)
				}
				ok, err := confirm(cmd, promptOut, question)
				if err != nil {
					return err
				}
				if !ok {
					return errs.New(errs.KindUsage, "uninstall cancelled; nothing was removed")
				}
			}
			var raw json.RawMessage
			path := pluginPath(id, "") + "?purge_data=" + strconv.FormatBool(purgeData)
			if err := client.Delete(cmd.Context(), path, &raw); err != nil {
				return err
			}
			out := cmd.OutOrStdout()
			if printJSONOutput(out, output, raw) {
				return nil
			}
			var resp struct {
				AutomationsDeleted int `json:"automations_deleted"`
			}
			_ = json.Unmarshal(raw, &resp)
			data := "data kept"
			if purgeData {
				data = "data deleted"
			}
			fmt.Fprintf(out, "uninstalled %s (%d automation%s deleted, %s)\n",
				id, resp.AutomationsDeleted, pluralS(resp.AutomationsDeleted), data)
			return nil
		},
	}
	cmd.Flags().BoolVarP(&yes, "yes", "y", false, "uninstall without asking for confirmation")
	cmd.Flags().BoolVar(&purgeData, "purge-data", false, "also delete the plugin's storage, settings and data directory")
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

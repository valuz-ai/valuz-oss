package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/apppluginpkg"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/auth"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
)

// ── control plane: publish / submissions ───────────────────────────────────

// cloudIdentity is what the CLI knows about the publishing account (for the
// summary; the server derives the org from the credential itself).
type cloudIdentity struct {
	OrgName      string
	Distribution string
}

// cloudClient builds a control-plane client. Credential order: --api-key,
// env VALUZ_API_KEY (a vzp_ personal key, sent as the bearer — it stands in
// for a session on the control plane), then the injected token or the
// `valuz auth login` state (refreshed when expired). The org is the one
// bound to the credential; there is no org parameter.
func cloudClient(cmd *cobra.Command, apiKey string) (*backend.ControlClient, cloudIdentity, error) {
	opts, err := Options(cmd)
	if err != nil {
		return nil, cloudIdentity{}, err
	}
	key := strings.TrimSpace(apiKey)
	if key == "" {
		key = strings.TrimSpace(os.Getenv("VALUZ_API_KEY"))
	}
	var ident cloudIdentity
	token := key
	if key != "" {
		if !strings.HasPrefix(key, "vzp_") {
			return nil, ident, errs.New(errs.KindUsage, "--api-key / VALUZ_API_KEY must be a personal API key (vzp_…)")
		}
	} else {
		token, err = resolveBearer(opts)
		if err != nil {
			return nil, ident, err
		}
		if token == "" {
			return nil, ident, errs.New(errs.KindAuth,
				"not logged in to Valuz: run `valuz auth login`, or pass --api-key / set VALUZ_API_KEY (vzp_…)")
		}
		if opts.Token == "" {
			if pair, _ := auth.NewStore().Load(); pair != nil {
				ident = cloudIdentity{OrgName: pair.Principal.OrgName, Distribution: pair.Principal.Distribution}
			}
		}
	}
	c := backend.NewControlClient(opts.CloudURL, token)
	c.ExtraHeaders = clientHeaders()
	return c, ident, nil
}

func newPluginPublishCmd() *cobra.Command {
	var (
		output        string
		scope         string
		distributions []string
		notes         string
		yes           bool
		apiKey        string
	)
	cmd := &cobra.Command{
		Use:   "publish <zip>",
		Short: "Publish a packed app plugin to your personal, org or global catalog",
		Long: "Upload a packed app plugin (see `valuz plugin app pack`) to the control plane.\n" +
			"Personal publishes go live after the automatic checks; org and global\n" +
			"submissions wait for review. Uses the `valuz auth login` state, or a\n" +
			"personal API key (--api-key / VALUZ_API_KEY, vzp_…) e.g. in CI.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			switch scope {
			case "personal", "org", "global":
			case "":
				return errs.New(errs.KindUsage, "--scope is required (personal|org|global)")
			default:
				return errs.New(errs.KindUsage, "unsupported --scope %q (want personal|org|global)", scope)
			}
			if scope == "global" && len(distributions) == 0 {
				return errs.New(errs.KindUsage, "--scope global needs at least one --distribution <id>")
			}
			if scope != "global" && len(distributions) > 0 {
				return errs.New(errs.KindUsage, "--distribution only applies to --scope global")
			}
			zipPath, err := filepath.Abs(args[0])
			if err != nil {
				return errs.Wrap(errs.KindUsage, err, "resolve %s", args[0])
			}
			data, err := os.ReadFile(zipPath)
			if err != nil {
				return errs.Wrap(errs.KindUsage, err, "read %s", args[0])
			}
			res, err := apppluginpkg.ValidateZipBytes(data)
			if err != nil {
				return errs.Wrap(errs.KindUsage, err, "%s: %v", args[0], err)
			}
			out := cmd.OutOrStdout()
			if !res.OK {
				if !printJSONOutput(out, output, res) {
					printValidation(out, res)
				}
				return invalidPluginError(res)
			}

			client, ident, err := cloudClient(cmd, apiKey)
			if err != nil {
				return err
			}
			sum := manifestSummary(res.Manifest, zipPath, apppluginpkg.SHA256Hex(data), int64(len(data)))
			sum.warnings = res.Warnings
			if prevVersion, prevPerms, ok := previousPublished(cmd.Context(), client, scope, res.ID()); ok {
				sum.updatesFrom = prevVersion
				sum.added = addedPermissions(prevPerms, sum.permissions)
			}
			sum.extra = [][2]string{{"scope", scope}, {"target", publishTarget(scope, distributions, ident)}}
			if notes != "" {
				sum.extra = append(sum.extra, [2]string{"notes", notes})
			}

			jsonOut := output == "json"
			summaryOut := out
			if jsonOut {
				summaryOut = cmd.ErrOrStderr()
			}
			if !jsonOut || !yes {
				printPackageSummary(summaryOut, sum)
			}
			if !yes {
				question := fmt.Sprintf("Publish %s %s to %s? [y/N] ", sum.id, sum.version, publishTarget(scope, distributions, ident))
				ok, err := confirm(cmd, summaryOut, question)
				if err != nil {
					return err
				}
				if !ok {
					return errs.New(errs.KindUsage, "publish cancelled; nothing was uploaded")
				}
			}

			fields := []backend.MultipartField{{Name: "scope", Value: scope}}
			for _, d := range distributions {
				fields = append(fields, backend.MultipartField{Name: "distribution_ids", Value: d})
			}
			if notes != "" {
				fields = append(fields, backend.MultipartField{Name: "notes", Value: notes})
			}
			file := backend.MultipartFile{
				Field:       "file",
				FileName:    filepath.Base(zipPath),
				ContentType: "application/zip",
				Data:        data,
			}
			var raw json.RawMessage
			if err := client.PostMultipart(cmd.Context(), submissionsAPI, fields, file, &raw); err != nil {
				return err
			}
			var sub map[string]any
			_ = json.Unmarshal(raw, &sub)
			if !printJSONOutput(out, output, raw) {
				printSubmission(out, sub)
			}
			if status, _ := sub["status"].(string); status == "rejected" {
				return errs.New(errs.KindUsage, "submission rejected by the automatic checks")
			}
			return nil
		},
	}
	f := cmd.Flags()
	f.StringVar(&scope, "scope", "", "catalog to publish to: personal|org|global")
	f.StringArrayVar(&distributions, "distribution", nil, "target distribution id for --scope global (repeatable)")
	f.StringVar(&notes, "notes", "", "release notes for the reviewers")
	f.BoolVarP(&yes, "yes", "y", false, "publish without asking for confirmation (CI)")
	f.StringVar(&apiKey, "api-key", "", "personal API key vzp_… (default: env VALUZ_API_KEY, then the valuz auth login state)")
	f.StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

func publishTarget(scope string, distributions []string, ident cloudIdentity) string {
	switch scope {
	case "personal":
		return "your personal catalog"
	case "org":
		if ident.OrgName != "" {
			return fmt.Sprintf("org %q", ident.OrgName)
		}
		return "your current org"
	}
	return "the global marketplace (distributions: " + strings.Join(distributions, ", ") + ")"
}

// previousPublished looks the app plugin up in the scope's catalog (best
// effort) to show the version it updates and the permissions it adds.
func previousPublished(ctx context.Context, client *backend.ControlClient, scope, id string) (string, []string, bool) {
	if id == "" {
		return "", nil, false
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	var raw any
	if err := client.Get(ctx, catalogAPI+"?scope="+url.QueryEscape(scope), &raw); err != nil {
		return "", nil, false
	}
	for _, entry := range listItems(raw, "items", "app_plugins") {
		e, _ := entry.(map[string]any)
		if e == nil || e["app_plugin_id"] != id {
			continue
		}
		bestVersion := ""
		var bestPerms []string
		versions, _ := e["versions"].([]any)
		for _, v := range versions {
			vm, _ := v.(map[string]any)
			ver, _ := vm["version"].(string)
			if ver == "" || (bestVersion != "" && apppluginpkg.CompareVersions(ver, bestVersion) <= 0) {
				continue
			}
			bestVersion = ver
			bestPerms = apppluginpkg.StringList(vm, "permissions")
		}
		if bestVersion == "" {
			if latest, ok := e["latest_version"].(string); ok && latest != "" {
				return latest, nil, false
			}
			return "", nil, false
		}
		return bestVersion, bestPerms, true
	}
	return "", nil, false
}

func addedPermissions(prev, cur []string) []string {
	had := map[string]bool{}
	for _, p := range prev {
		had[p] = true
	}
	var out []string
	for _, p := range cur {
		if !had[p] {
			out = append(out, p)
		}
	}
	return out
}

func printSubmission(out io.Writer, sub map[string]any) {
	fmt.Fprintf(out, "\nsubmission %s\n", strOr(sub["id"], strOr(sub["submission_id"], "-")))
	fmt.Fprintf(out, "status     %s\n", strOr(sub["status"], "-"))
	for _, key := range []string{"reason", "review_reason", "feedback"} {
		if s, ok := sub[key].(string); ok && s != "" {
			fmt.Fprintf(out, "reason     %s\n", s)
			break
		}
	}
	checks := listItems(sub["checks"])
	if len(checks) == 0 {
		checks = listItems(sub["check_results"])
	}
	if len(checks) == 0 {
		return
	}
	fmt.Fprintln(out, "checks:")
	for _, c := range checks {
		cm, ok := c.(map[string]any)
		if !ok {
			fmt.Fprintf(out, "  %v\n", c)
			continue
		}
		state := strOr(cm["status"], "")
		if state == "" {
			if passed, ok := cm["ok"].(bool); ok {
				state = map[bool]string{true: "pass", false: "fail"}[passed]
			} else if passed, ok := cm["passed"].(bool); ok {
				state = map[bool]string{true: "pass", false: "fail"}[passed]
			}
		}
		line := fmt.Sprintf("  %-6s %s", orDash(state), strOr(cm["name"], strOr(cm["id"], "")))
		if msg := strOr(cm["message"], strOr(cm["detail"], "")); msg != "" {
			line += ": " + msg
		}
		fmt.Fprintln(out, strings.TrimRight(line, " "))
	}
}

func newPluginSubmissionsCmd() *cobra.Command {
	var (
		output string
		apiKey string
	)
	cmd := &cobra.Command{
		Use:   "submissions",
		Short: "List your app plugin submissions and their review status",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if err := checkOutputFormat(output); err != nil {
				return err
			}
			client, _, err := cloudClient(cmd, apiKey)
			if err != nil {
				return err
			}
			var raw json.RawMessage
			if err := client.Get(cmd.Context(), submissionsAPI, &raw); err != nil {
				return err
			}
			out := cmd.OutOrStdout()
			if printJSONOutput(out, output, raw) {
				return nil
			}
			var doc any
			_ = json.Unmarshal(raw, &doc)
			items := listItems(doc, "items", "submissions")
			if len(items) == 0 {
				fmt.Fprintln(out, "(no submissions)")
				return nil
			}
			tw := tabwriter.NewWriter(out, 0, 0, 2, ' ', 0)
			fmt.Fprintln(tw, "ID\tEXTENSION\tVERSION\tSCOPE\tSTATUS\tSUBMITTED")
			for _, it := range items {
				m, _ := it.(map[string]any)
				fmt.Fprintf(tw, "%s\t%s\t%s\t%s\t%s\t%s\n",
					strOr(m["id"], "-"),
					strOr(m["app_plugin_id"], "-"),
					strOr(m["version"], "-"),
					strOr(m["scope"], "-"),
					strOr(m["status"], "-"),
					strOr(m["created_at"], strOr(m["submitted_at"], "-")))
			}
			return tw.Flush()
		},
	}
	cmd.Flags().StringVar(&apiKey, "api-key", "", "personal API key vzp_… (default: env VALUZ_API_KEY, then the valuz auth login state)")
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

// listItems returns v when it is a list, else the first list found under keys.
func listItems(v any, keys ...string) []any {
	if arr, ok := v.([]any); ok {
		return arr
	}
	m, _ := v.(map[string]any)
	for _, k := range keys {
		if arr, ok := m[k].([]any); ok {
			return arr
		}
	}
	return nil
}

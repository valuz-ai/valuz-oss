package cmd

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"sort"
	"strings"

	"github.com/spf13/cobra"

	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/pluginpkg"
)

// ── shared rendering ────────────────────────────────────────────────────────

// packageSummary is the install/publish confirmation summary (the same
// facts the desktop confirmation card shows, plugin-development 04 §1).
type packageSummary struct {
	id, version, name, publisher, source, sha256 string
	size                                         int64
	updatesFrom                                  string
	permissions, added                           []string
	requires, unmet                              []string
	hasBackend                                   bool
	warnings, errors                             []string
	extra                                        [][2]string
}

func manifestSummary(m map[string]any, source, sha string, size int64) packageSummary {
	id, _ := m["id"].(string)
	version, _ := m["version"].(string)
	return packageSummary{
		id:          id,
		version:     version,
		name:        pluginpkg.LocalizedText(m["name"]),
		publisher:   publisherName(m["publisher"]),
		source:      source,
		sha256:      sha,
		size:        size,
		permissions: pluginpkg.StringList(m, "permissions"),
		requires:    pluginpkg.StringList(m, "requires"),
	}
}

func inspectSummary(insp inspectResult, source string) packageSummary {
	sum := manifestSummary(insp.Manifest, source, insp.SHA256, insp.Size)
	if insp.Permissions != nil {
		sum.permissions = insp.Permissions
	}
	if insp.Requires != nil {
		sum.requires = insp.Requires
	}
	sum.unmet = insp.UnmetRequires
	sum.added = insp.AddedPermissions
	sum.hasBackend = insp.HasBackend
	if insp.Existing != nil {
		sum.updatesFrom = insp.Existing.Version
	}
	sum.warnings = findingTexts(insp.Warnings)
	sum.errors = findingTexts(insp.Errors)
	return sum
}

// findingTexts renders validation findings (strings or {path?, message}).
func findingTexts(items []any) []string {
	out := make([]string, 0, len(items))
	for _, item := range items {
		switch v := item.(type) {
		case string:
			out = append(out, v)
		case map[string]any:
			text := strOr(v["message"], compactAny(v))
			if p, ok := v["path"].(string); ok && p != "" {
				text = p + ": " + text
			}
			out = append(out, text)
		default:
			out = append(out, fmt.Sprint(v))
		}
	}
	return out
}

func compactAny(v any) string {
	raw, err := json.Marshal(v)
	if err != nil {
		return fmt.Sprint(v)
	}
	return string(raw)
}

func printPackageSummary(out io.Writer, s packageSummary) {
	row := func(k, v string) { fmt.Fprintf(out, "%-12s %s\n", k+":", v) }
	row("plugin", orDash(s.id))
	version := orDash(s.version)
	if s.updatesFrom != "" {
		version += " (updates " + s.updatesFrom + ")"
	}
	row("version", version)
	if s.name != "" {
		row("name", s.name)
	}
	row("publisher", orDash(s.publisher))
	row("source", s.source)
	row("sha256", orDash(s.sha256))
	if s.size > 0 {
		row("size", fmt.Sprintf("%d bytes", s.size))
	}
	added := map[string]bool{}
	for _, p := range s.added {
		added[p] = true
	}
	perms := make([]string, 0, len(s.permissions))
	for _, p := range s.permissions {
		if added[p] {
			p += " (new)"
		}
		perms = append(perms, p)
	}
	row("permissions", listOrNone(perms))
	unmet := map[string]bool{}
	for _, r := range s.unmet {
		unmet[r] = true
	}
	reqs := make([]string, 0, len(s.requires))
	for _, r := range s.requires {
		if unmet[r] {
			r += " (unmet)"
		}
		reqs = append(reqs, r)
	}
	row("requires", listOrNone(reqs))
	if s.hasBackend {
		row("backend", "yes")
	}
	for _, kv := range s.extra {
		row(kv[0], kv[1])
	}
	for _, w := range s.warnings {
		fmt.Fprintf(out, "warning: %s\n", w)
	}
	for _, e := range s.errors {
		fmt.Fprintf(out, "error: %s\n", e)
	}
}

// confirm asks question on out and reads a y/N answer from the command's
// stdin; anything but y/yes (including EOF) is a no.
func confirm(cmd *cobra.Command, out io.Writer, question string) (bool, error) {
	fmt.Fprint(out, question)
	line, err := bufio.NewReader(cmd.InOrStdin()).ReadString('\n')
	if err != nil && !errors.Is(err, io.EOF) {
		return false, errs.Wrap(errs.KindUsage, err, "read confirmation")
	}
	if errors.Is(err, io.EOF) {
		fmt.Fprintln(out)
	}
	switch strings.ToLower(strings.TrimSpace(line)) {
	case "y", "yes":
		return true, nil
	}
	return false, nil
}

func statusText(p pluginItem) string {
	s := orDash(p.Status)
	if p.StatusReason != "" {
		s += " (" + p.StatusReason + ")"
	}
	return s
}

func revisionText(v any) string {
	if v == nil {
		return "-"
	}
	return fmt.Sprint(v)
}

func publisherName(v any) string {
	switch p := v.(type) {
	case string:
		return p
	case map[string]any:
		s, _ := p["name"].(string)
		return s
	}
	return ""
}

// sourceKind is the short source label for tables (dev, file, url, catalog:<scope>).
func sourceKind(v any) string {
	switch src := v.(type) {
	case string:
		return orDash(src)
	case map[string]any:
		kind := strOr(src["kind"], "-")
		if scope, ok := src["scope"].(string); ok && scope != "" {
			kind += ":" + scope
		}
		return kind
	}
	return "-"
}

func sourceDetail(v any) string {
	src, ok := v.(map[string]any)
	if !ok {
		return sourceKind(v)
	}
	detail := sourceKind(v)
	for _, key := range []string{"path", "url", "item_id"} {
		if s, ok := src[key].(string); ok && s != "" {
			return detail + " " + s
		}
	}
	return detail
}

func automationNames(items []any) []string {
	var names []string
	for _, a := range items {
		if m, ok := a.(map[string]any); ok {
			if n, ok := m["name"].(string); ok && n != "" {
				names = append(names, n)
			}
		}
	}
	sort.Strings(names)
	return names
}

func listOrNone(items []string) string {
	if len(items) == 0 {
		return "(none)"
	}
	return strings.Join(items, ", ")
}

func orDash(s string) string {
	if s == "" {
		return "-"
	}
	return s
}

func pluralS(n int) string {
	if n == 1 {
		return ""
	}
	return "s"
}

package cmd

import (
	"fmt"
	"net/url"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/config"
	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
)

// Named execution environments. The commercial product routes per-entity
// between local (Runtime Backend) and cloud (Cloud Runtime) targets
// (frontend/packages/commercial/src/lib/execution-location.ts); the CLI
// exposes the same targets as named environments that pin the backend
// base URL. The token store is shared — the control-plane JWT works for
// both targets.
const (
	envLocal = "local"
	envCloud = "cloud"
)

// envBackendURLs holds the default backend base per environment. Cloud's
// real URL is deployment-specific; `valuz env set` overrides it.
var envBackendURLs = map[string]string{
	envLocal: "http://127.0.0.1:8000",
	envCloud: "", // configured via `valuz env set cloud --url ...`
}

// newEnvCmd builds `valuz env ...` — execution-environment switching
// (local / cloud), persisted in the active profile's non-sensitive
// defaults.
func newEnvCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "env",
		Short: "Manage execution environments (local / cloud)",
	}
	cmd.AddCommand(
		newEnvListCmd(),
		newEnvShowCmd(),
		newEnvUseCmd(),
		newEnvSetCmd(),
	)
	return cmd
}

func newEnvListCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "List known environments",
		RunE: func(cmd *cobra.Command, _ []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			profile, err := opts.ResolveProfile()
			if err != nil {
				return err
			}
			current := profile.Default("ENV")
			if current == "" {
				current = envLocal
			}
			for _, name := range []string{envLocal, envCloud} {
				url := profile.Default("BACKEND_URL_" + name)
				if url == "" {
					url = envBackendURLs[name]
				}
				marker := " "
				if name == current {
					marker = "*"
				}
				fmt.Fprintf(cmd.OutOrStdout(), "%s %-6s  %s\n", marker, name, url)
			}
			return nil
		},
	}
}

func newEnvShowCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "show",
		Short: "Show the active environment",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			profile, err := opts.ResolveProfile()
			if err != nil {
				return err
			}
			current := profile.Default("ENV")
			if current == "" {
				current = envLocal
			}
			url := profile.Default("BACKEND_URL_" + current)
			if url == "" {
				url = envBackendURLs[current]
			}
			fmt.Fprintf(cmd.OutOrStdout(), "environment: %s\nbackend:     %s\n", current, url)
			return nil
		},
	}
}

func newEnvUseCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "use <local|cloud>",
		Short: "Switch the active execution environment",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := rejectIfManaged("env use"); err != nil {
				return err
			}
			name := args[0]
			if _, ok := envBackendURLs[name]; !ok {
				return errs.New(errs.KindUsage, "unknown environment %q (want local|cloud)", name)
			}
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			profile, err := opts.ResolveProfile()
			if err != nil {
				return err
			}
			url := profile.Default("BACKEND_URL_" + name)
			if name == envCloud && url == "" {
				return errs.New(errs.KindUsage,
					"cloud has no backend URL pinned; run `valuz env set cloud --url <backend-base>` first")
			}
			if profile.Defaults == nil {
				profile.Defaults = map[string]string{}
			}
			profile.Defaults["ENV"] = name
			store := config.NewProfileStore("")
			if err := store.Save(profile); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "switched to %s (%s)\n", name, url)
			return nil
		},
	}
}

func newEnvSetCmd() *cobra.Command {
	var url string
	cmd := &cobra.Command{
		Use:   "set <local|cloud> --url <backend-base>",
		Short: "Pin a custom backend base URL for an environment",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := rejectIfManaged("env set"); err != nil {
				return err
			}
			name := args[0]
			if _, ok := envBackendURLs[name]; !ok {
				return errs.New(errs.KindUsage, "unknown environment %q (want local|cloud)", name)
			}
			if url == "" {
				return errs.New(errs.KindUsage, "--url is required")
			}
			if err := validateBackendURL(url); err != nil {
				return err
			}
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
			profile.Defaults["BACKEND_URL_"+name] = url
			store := config.NewProfileStore("")
			if err := store.Save(profile); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "%s -> %s\n", name, url)
			return nil
		},
	}
	cmd.Flags().StringVar(&url, "url", "", "backend base URL for this environment")
	return cmd
}

// validateBackendURL rejects values that would poison every later run:
// a base URL must be an absolute http(s) URL without a path.
func validateBackendURL(raw string) error {
	u, err := url.Parse(raw)
	if err != nil {
		return errs.Wrap(errs.KindUsage, err, "invalid --url %q", raw)
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return errs.New(errs.KindUsage, "--url must be an http(s) URL (got %q)", raw)
	}
	if u.Host == "" {
		return errs.New(errs.KindUsage, "--url must include a host (got %q)", raw)
	}
	return nil
}

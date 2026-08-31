package cmd

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/config"
	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/version"
)

var cliVersion = "dev"

// SetVersion is called by main() to inject the build-time version (via ldflags).
func SetVersion(v string) { cliVersion = v }

// Global flag names shared by the product CLI shell.
const (
	flagProfile    = "profile"
	flagBackendURL = "backend-url"
	flagDebug      = "debug"
	flagOutput     = "output"
	flagTokenFile  = "token-file"
	flagCloudURL   = "cloud-url"
)

// RootOptions carries the resolved product-shell options for a command.
// Commands read these instead of re-resolving flags themselves.
type RootOptions struct {
	// ProfileName is the named profile in use ("" = default).
	ProfileName string
	// BackendURL is the resolved backend base URL (may come from flag,
	// env, profile or discovery).
	BackendURL string
	// Debug enables the redacted error chain on stderr.
	Debug bool
	// Output is the machine-output protocol ("" = human).
	Output string
	// Token is the bearer credential for commercial backends
	// (VALUZ_BACKEND_TOKEN env or --token-file; never a raw flag).
	Token string
	// CloudURL is the control-plane base URL (login/refresh/identity).
	CloudURL string
}

// ResolveProfile loads the named profile via the product shell's store.
func (o *RootOptions) ResolveProfile() (*config.Profile, error) {
	return config.NewProfileStore("").Load(o.ProfileName)
}

// NewResolver builds a resolver bound to this invocation's profile.
func (o *RootOptions) NewResolver() (*config.Resolver, error) {
	p, err := o.ResolveProfile()
	if err != nil {
		return nil, err
	}
	return config.NewResolver(p), nil
}

// Root returns the configured root command. Tests and main() both call this
// rather than relying on a package-level singleton.
func Root() *cobra.Command {
	root := &cobra.Command{
		Use:           "valuz",
		Short:         "Valuz product CLI",
		Long:          "Valuz product CLI — start, stop, inspect, and drive local runtime services.",
		Version:       cliVersion,
		SilenceUsage:  true,
		SilenceErrors: true,
		// PersistentPreRun resolves the shell options once for every command
		// so handlers read consistent values (flag > env > profile > discovery).
		PersistentPreRunE: func(cmd *cobra.Command, _ []string) error {
			o, err := resolveRootOptions(cmd)
			if err != nil {
				return err
			}
			ctx := context.WithValue(cmd.Context(), shellOptsKey{}, o)
			cmd.SetContext(ctx)
			return nil
		},
	}
	pf := root.PersistentFlags()
	pf.String(flagProfile, "", "named profile (default: discovery/env only)")
	pf.String(flagBackendURL, "", "backend base URL override (default: env > profile > discovery)")
	pf.Bool(flagDebug, false, "verbose redacted error chains on stderr")
	pf.String(flagTokenFile, "", "read the bearer token from a file (0600; env VALUZ_BACKEND_TOKEN also accepted)")
	pf.String(flagCloudURL, "", "control-plane base URL (default: env VALUZ_CLOUD_URL or dev 127.0.0.1:8001/cloud)")
	root.AddCommand(
		newStartCmd(),
		newStopCmd(),
		newRestartCmd(),
		newStatusCmd(),
		newLogsCmd(),
		newDoctorCmd(),
		newInstallAutostartCmd(),
		newUninstallAutostartCmd(),
		newWebCmd(),
		newDesktopCmd(),
		newTUICmd(),
		newVersionCmd(),
		newRunCmd(),
		newSessionCmd(),
		newTaskCmd(),
		newProjectCmd(),
		newActivityCmd(),
		newResourceCmd(),
		newAuthCmd(),
		newEnvCmd(),
		newModelCmd(),
		newAgentCmd(),
	)
	return root
}

// shellOptsKey stores resolved options in the command context.
type shellOptsKey struct{}

// Options returns the resolved product-shell options for cmd, resolving
// them on demand when PersistentPreRunE did not run (tests, direct calls).
func Options(cmd *cobra.Command) (*RootOptions, error) {
	if o, ok := cmd.Context().Value(shellOptsKey{}).(*RootOptions); ok {
		return o, nil
	}
	return resolveRootOptions(cmd)
}

func resolveRootOptions(cmd *cobra.Command) (*RootOptions, error) {
	profile, err := cmd.Flags().GetString(flagProfile)
	if err != nil {
		return nil, fmt.Errorf("resolve %s: %w", flagProfile, err)
	}
	backendURL, err := cmd.Flags().GetString(flagBackendURL)
	if err != nil {
		return nil, fmt.Errorf("resolve %s: %w", flagBackendURL, err)
	}
	debug, err := cmd.Flags().GetBool(flagDebug)
	if err != nil {
		return nil, fmt.Errorf("resolve %s: %w", flagDebug, err)
	}
	output, _ := cmd.Flags().GetString(flagOutput)
	tokenFile, _ := cmd.Flags().GetString(flagTokenFile)

	token, err := resolveToken(tokenFile)
	if err != nil {
		return nil, err
	}
	cloudURL := os.Getenv("VALUZ_CLOUD_URL")
	if cloudURL == "" {
		cloudURL = "http://127.0.0.1:8001/cloud"
	}

	resolver, err := (&RootOptions{ProfileName: profile}).NewResolver()
	if err != nil {
		return nil, err
	}
	resolvedURL, err := resolver.BackendURL(backendURL)
	if err != nil {
		return nil, err
	}
	return &RootOptions{
		ProfileName: profile,
		BackendURL:  resolvedURL,
		Debug:       debug,
		Output:      output,
		Token:       token,
		CloudURL:    cloudURL,
	}, nil
}

// resolveToken reads the bearer credential: env VALUZ_BACKEND_TOKEN wins,
// then --token-file (trimmed, must not be empty). The token is never
// printed, logged or rendered (design §7).
func resolveToken(tokenFile string) (string, error) {
	if v := os.Getenv("VALUZ_BACKEND_TOKEN"); v != "" {
		return strings.TrimSpace(v), nil
	}
	if tokenFile == "" {
		return "", nil
	}
	data, err := os.ReadFile(tokenFile)
	if err != nil {
		return "", errs.Wrap(errs.KindUsage, err, "read --token-file %s", tokenFile)
	}
	tok := strings.TrimSpace(string(data))
	if tok == "" {
		return "", errs.New(errs.KindUsage, "--token-file %s is empty", tokenFile)
	}
	return tok, nil
}

// Execute runs the root command and renders errors through the shell's
// single exit boundary. Returns the process exit code.
func Execute(args []string, stdout, stderr io.Writer) int {
	root := Root()
	root.SetArgs(args)
	root.SetOut(stdout)
	root.SetErr(stderr)
	if err := root.Execute(); err != nil {
		var ece *errs.ExitCodeError
		if errors.As(err, &ece) {
			fmt.Fprintln(stderr, ece.Message)
			return ece.Code
		}
		debug := false
		if o, rerr := Options(root); rerr == nil {
			debug = o.Debug
		}
		renderer := errs.Renderer{Debug: debug}
		fmt.Fprintln(stderr, renderer.Render(err))
		return errs.KindOf(err).ExitCode()
	}
	return 0
}

func newVersionCmd() *cobra.Command {
	var output string
	cmd := &cobra.Command{
		Use:   "version",
		Short: "Print the CLI version",
		RunE: func(cmd *cobra.Command, _ []string) error {
			info := version.Current(true)
			switch output {
			case "", "human":
				fmt.Fprintln(cmd.OutOrStdout(), info.String())
			case "json":
				data, err := info.JSON()
				if err != nil {
					return errs.Wrap(errs.KindInternal, err, "encode version JSON")
				}
				fmt.Fprintln(cmd.OutOrStdout(), string(data))
			default:
				return errs.New(errs.KindUsage, "unsupported --output %q (want human|json)", output)
			}
			return nil
		},
	}
	cmd.Flags().StringVarP(&output, flagOutput, "o", "", "output format: human|json")
	return cmd
}

// silentExit wraps os.Exit so tests can exercise the exit boundary.
var _ = os.Exit

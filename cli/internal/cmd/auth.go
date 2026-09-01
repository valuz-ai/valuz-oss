package cmd

import (
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/spf13/cobra"
	"golang.org/x/term"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/auth"
	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
)

// newAuthCmd builds `valuz auth ...` — the CLI's login state (human-local:
// managed execution contexts must use scoped credentials and never fall
// back to this store, design.md §7).
func newAuthCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "auth",
		Short: "Login state for commercial backends (control-plane identity)",
	}
	cmd.AddCommand(
		newAuthLoginCmd(),
		newAuthStatusCmd(),
		newAuthLogoutCmd(),
	)
	return cmd
}

func newAuthLoginCmd() *cobra.Command {
	var (
		email         string
		apiKey        string
		clientID      string
		resource      string
		passwordStdin bool
	)
	cmd := &cobra.Command{
		Use:   "login",
		Short: "Log in to the control plane (email+password or vzp_ api key)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if err := rejectIfManaged("auth login"); err != nil {
				return err
			}
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			store := auth.NewStore()
			client := auth.NewClient(opts.CloudURL)

			if apiKey != "" {
				pair, err := client.LoginWithAPIKey(apiKey)
				if err != nil {
					return err
				}
				if err := store.Save(pair); err != nil {
					return err
				}
				fmt.Fprintf(cmd.OutOrStdout(), "logged in as %s (distribution %s)\n",
					display(pair), pair.Principal.Distribution)
				return nil
			}

			if email == "" {
				return errs.New(errs.KindUsage, "--email or --api-key is required")
			}
			password, err := readPassword(cmd, passwordStdin)
			if err != nil {
				return err
			}
			if clientID == "" {
				clientID = os.Getenv("VALUZ_OAUTH_CLIENT_ID")
			}
			if clientID == "" {
				return errs.New(errs.KindUsage,
					"--client-id is required (or set VALUZ_OAUTH_CLIENT_ID)")
			}

			pair, err := client.Login(email, password, clientID, resource)
			if err != nil {
				return err
			}
			if err := store.Save(pair); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "logged in as %s (distribution %s, org %s)\n",
				display(pair), pair.Principal.Distribution, pair.Principal.OrgName)
			return nil
		},
	}
	f := cmd.Flags()
	f.StringVar(&email, "email", "", "account email")
	f.StringVar(&apiKey, "api-key", "", "personal api key (vzp_…); preferred for headless")
	f.StringVar(&clientID, "client-id", "", "oauth client id (default: VALUZ_OAUTH_CLIENT_ID)")
	f.StringVar(&resource, "resource", "", "RFC 8707 resource (distribution tenant hint)")
	f.BoolVar(&passwordStdin, "password-stdin", false, "read the password from stdin (no prompt)")
	return cmd
}

func newAuthStatusCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "status",
		Short: "Show the current login state",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			pair, err := auth.NewStore().Load()
			if err != nil {
				return err
			}
			if pair == nil || pair.AccessToken == "" {
				fmt.Fprintln(cmd.OutOrStdout(), "not logged in")
				return nil
			}
			if pair.Expired() {
				fmt.Fprintf(cmd.OutOrStdout(),
					"logged in (access token expired; the next command refreshes it against the control plane %s)\n",
					opts.CloudURL)
			} else {
				fmt.Fprintln(cmd.OutOrStdout(), "logged in")
			}
			fmt.Fprintf(cmd.OutOrStdout(), "account:      %s\n", display(pair))
			fmt.Fprintf(cmd.OutOrStdout(), "distribution: %s\n", pair.Principal.Distribution)
			fmt.Fprintf(cmd.OutOrStdout(), "org:          %s\n", pair.Principal.OrgName)
			fmt.Fprintf(cmd.OutOrStdout(), "role:         %s\n", pair.Principal.Role)
			return nil
		},
	}
}

func newAuthLogoutCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "logout",
		Short: "Revoke the login and clear stored tokens",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if err := rejectIfManaged("auth logout"); err != nil {
				return err
			}
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			store := auth.NewStore()
			pair, err := store.Load()
			if err != nil {
				return err
			}
			if pair != nil && pair.RefreshToken != "" {
				_ = auth.NewClient(opts.CloudURL).Revoke(pair.RefreshToken) // best-effort
			}
			if err := store.Clear(); err != nil {
				return err
			}
			fmt.Fprintln(cmd.OutOrStdout(), "logged out")
			return nil
		},
	}
}

// readPassword reads the password: with --password-stdin from the piped
// stdin (never echoes, never stores); otherwise — when stdin is a TTY —
// with a masked prompt via the terminal API. Piping on a TTY without
// --password-stdin (io.ReadAll would block until EOF) is rejected.
func readPassword(cmd *cobra.Command, passwordStdin bool) (string, error) {
	if passwordStdin {
		data, err := io.ReadAll(io.LimitReader(cmd.InOrStdin(), 4096))
		if err != nil {
			return "", errs.Wrap(errs.KindUsage, err, "read password from stdin")
		}
		pw := strings.TrimRight(string(data), "\r\n")
		if pw == "" {
			return "", errs.New(errs.KindUsage, "empty password (pipe it via --password-stdin)")
		}
		return pw, nil
	}
	in, ok := cmd.InOrStdin().(*os.File)
	if !ok || !term.IsTerminal(int(in.Fd())) {
		return "", errs.New(errs.KindUsage,
			"password input requires a terminal; pipe it with --password-stdin")
	}
	fmt.Fprint(cmd.OutOrStdout(), "Password: ")
	raw, err := term.ReadPassword(int(in.Fd()))
	fmt.Fprintln(cmd.OutOrStdout())
	if err != nil {
		return "", errs.Wrap(errs.KindUsage, err, "read password")
	}
	pw := string(raw)
	if pw == "" {
		return "", errs.New(errs.KindUsage, "empty password")
	}
	return pw, nil
}

func display(pair *auth.TokenPair) string {
	if pair.DisplayName != "" {
		return pair.DisplayName
	}
	if pair.Email != "" {
		return pair.Email
	}
	if pair.Phone != "" {
		return pair.Phone
	}
	return pair.Principal.MasterID
}

package cmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"time"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/auth"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
)

// sessionCreateOpts carries the functional create-session options shared
// by `valuz session create` and the run path.
type sessionCreateOpts struct {
	Title          string
	AgentSlug      string
	ModelID        string
	ProviderID     string
	RuntimeID      string
	PermissionMode string
	MCPSlugs       []string
	SkillIDs       []string
}

// createSession creates a session and attaches extra skills when given —
// a functional wrapper over POST /v1/sessions + PUT /v1/sessions/{id}/skills.
func createSession(client *backend.ControlClient, cmd *cobra.Command, projectID string, o sessionCreateOpts) (*backend.SessionDetail, error) {
	perm := o.PermissionMode
	if perm == "" {
		perm = "default"
	}
	body := backend.SessionCreateRequest{ProjectID: projectID, PermissionMode: &perm, MCPSlugs: o.MCPSlugs}
	if o.Title != "" {
		body.Title = &o.Title
	}
	if o.ModelID != "" {
		body.ModelID = &o.ModelID
	}
	if o.ProviderID != "" {
		body.ProviderID = &o.ProviderID
	}
	if o.RuntimeID != "" {
		body.RuntimeID = &o.RuntimeID
	}
	if o.AgentSlug != "" {
		body.AgentSlug = &o.AgentSlug
	}

	var s backend.SessionDetail
	if err := client.Post(cmd.Context(), "/v1/sessions", body, &s); err != nil {
		return nil, err
	}
	if len(o.SkillIDs) > 0 {
		var resp backend.SessionSkillsResponse
		if err := client.Put(cmd.Context(), "/v1/sessions/"+s.ID+"/skills",
			backend.SessionSkillsRequest{SkillIDs: o.SkillIDs}, &resp); err != nil {
			return nil, err
		}
	}
	return &s, nil
}

// frameJSON renders an SSE frame as a compact JSON document for --output jsonl.
func frameJSON(f *backend.SSEFrame) ([]byte, error) {
	return json.Marshal(f)
}

// resolveBearer returns the effective bearer credential for a command:
// explicit injection (env/token-file) wins; otherwise the stored login is
// loaded and refreshed when expired. Returns "" for the OSS local path.
//
// The refresh path holds a file lock so two concurrent commands cannot
// refresh the same pair at once (last-writer-wins on a rotated refresh
// token would strand the loser). After acquiring the lock the store is
// re-read: the winner's fresh pair is used when present.
// isManaged reports whether the CLI runs in a managed execution context
// (e.g. a scheduled job or automation). Managed contexts must use
// execution-scoped credentials and must never fall back to a human login.
func isManaged() bool {
	return os.Getenv("VALUZ_MANAGED") == "1"
}

// rejectIfManaged blocks human-local commands (auth login/logout, env
// switching) under a managed context — design.md §7 fail-closed.
func rejectIfManaged(cmdName string) error {
	if isManaged() {
		return errs.New(errs.KindAuth,
			"%s is disabled in managed contexts (execution-scoped credentials only)", cmdName)
	}
	return nil
}

func resolveBearer(opts *RootOptions) (string, error) {
	if isManaged() {
		// Managed: only an injected scoped token is acceptable; human
		// login state must not be used.
		if opts == nil || opts.Token == "" {
			return "", errs.New(errs.KindAuth,
				"managed context requires an injected token (VALUZ_BACKEND_TOKEN or --token-file)")
		}
		return opts.Token, nil
	}
	if opts != nil && opts.Token != "" {
		return opts.Token, nil
	}
	store := auth.NewStore()
	pair, err := store.Load()
	if err != nil {
		return "", err
	}
	if pair == nil || pair.AccessToken == "" {
		return "", nil
	}
	if !pair.Expired() {
		return pair.AccessToken, nil
	}
	if pair.RefreshToken == "" {
		return "", nil
	}

	unlock, err := lockAuthRefresh()
	if err != nil {
		return "", err
	}
	defer unlock()

	// Re-read under the lock: another process may have refreshed already.
	pair, err = store.Load()
	if err != nil {
		return "", err
	}
	if pair == nil || pair.AccessToken == "" {
		return "", nil
	}
	if !pair.Expired() {
		return pair.AccessToken, nil
	}

	cloudURL := ""
	if opts != nil {
		cloudURL = opts.CloudURL
	}
	client := auth.NewClient(cloudURL)
	renewed, err := client.Refresh(pair.RefreshToken)
	if err != nil {
		// The refresh target is the control plane, not the execution
		// backend — an unreachable control plane must not masquerade as
		// a dead backend (different URL, different fix).
		return "", errs.Wrap(errs.KindOf(err), err,
			"token refresh failed: control plane unreachable at %s (check VALUZ_CLOUD_URL / --cloud-url, or log in again)", cloudURL)
	}
	if err := store.Save(renewed); err != nil {
		return "", err
	}
	return renewed.AccessToken, nil
}

// lockAuthRefresh creates the auth lock file atomically (O_EXCL), waiting
// up to ~3s for a concurrent refresher to finish.
func lockAuthRefresh() (func(), error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return func() {}, nil
	}
	lockPath := filepath.Join(home, ".valuz-oss", "auth.json.lock")
	for i := 0; i < 30; i++ {
		f, err := os.OpenFile(lockPath, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
		if err == nil {
			_ = f.Close()
			return func() { _ = os.Remove(lockPath) }, nil
		}
		if !os.IsExist(err) {
			return func() {}, nil // lock dir unavailable: proceed unlocked
		}
		time.Sleep(100 * time.Millisecond)
	}
	return func() {}, nil // timeout: proceed unlocked (refresh is idempotent-ish)
}

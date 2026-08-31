package cmd

import (
	"encoding/json"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/auth"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
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
func resolveBearer(opts *RootOptions) (string, error) {
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
	cloudURL := ""
	if opts != nil {
		cloudURL = opts.CloudURL
	}
	client := auth.NewClient(cloudURL)
	renewed, err := client.Refresh(pair.RefreshToken)
	if err != nil {
		return "", err
	}
	if err := store.Save(renewed); err != nil {
		return "", err
	}
	return renewed.AccessToken, nil
}

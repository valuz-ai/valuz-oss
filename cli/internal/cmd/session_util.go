package cmd

import (
	"encoding/json"

	"github.com/spf13/cobra"

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

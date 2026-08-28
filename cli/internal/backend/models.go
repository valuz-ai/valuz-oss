// Package backend holds the HTTP DTOs and the two clients the headless
// run path needs: a bounded ControlClient for request/response calls and a
// long-lived StreamClient for the SSE event stream. DTOs are hand-written
// mirrors of api/openapi.yaml (the repo's established pattern); contract
// drift tests land once the OpenAPI fixes are merged.
package backend

// ── DTOs (hand mirrors of api/openapi.yaml) ─────────────────────────

// SystemStatus is the readiness probe payload (GET /v1/system/status).
type SystemStatus struct {
	Status string `json:"status"`
}

// Project is the payload of GET/POST /v1/projects.
type Project struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	RootPath string `json:"root_path,omitempty"`
}

// ProjectList is the list response of GET /v1/projects.
type ProjectList struct {
	Projects []Project `json:"projects"`
}

// SessionCreateRequest mirrors POST /v1/sessions.
type SessionCreateRequest struct {
	ProjectID       string  `json:"project_id"`
	Title           *string `json:"title,omitempty"`
	ModelID         *string `json:"model_id,omitempty"`
	ProviderID      *string `json:"provider_id,omitempty"`
	RuntimeID       *string `json:"runtime_id,omitempty"`
	PermissionMode  *string `json:"permission_mode,omitempty"`
	AgentSlug       *string `json:"agent_slug,omitempty"`
}

// SessionMessageRequest mirrors POST /v1/sessions/{id}/messages.
type SessionMessageRequest struct {
	Prompt string `json:"prompt"`
}

// SessionDetail mirrors GET/POST /v1/sessions (subset used by the CLI).
type SessionDetail struct {
	ID            string  `json:"id"`
	ProjectID     string  `json:"project_id"`
	Status        string  `json:"status"`
	Runtime       string  `json:"runtime_provider"`
	PermissionMode string `json:"permission_mode"`
	AgentSlug     *string `json:"agent_slug"`
	ModelID       *string `json:"locked_model_id"`
}

// ── Error shapes ────────────────────────────────────────────────────

// ValuzError is the structured error body {"error": {code, message}}.
type ValuzError struct {
	Error struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
	} `json:"error"`
}

// DetailError is the FastAPI-style body {"detail": ...} (string, array or
// i18n object). Only the string form is used for classification.
type DetailError struct {
	Detail any `json:"detail"`
}

// ── SSE frame (flat wire shape, see SessionEventFrame in OpenAPI) ───

// SSEFrame is the data: payload of one session event-stream frame.
type SSEFrame struct {
	Seq       int               `json:"seq"`
	EventType *string           `json:"event_type"`
	Payload   map[string]string `json:"payload"`
	Timestamp *int64            `json:"timestamp"`
	EventUID  *string           `json:"event_uid"`
}

// IsHeartbeat reports whether the frame is a heartbeat (no event_type).
// Heartbeat seq is the durable history cursor — the only value safe to
// persist for reconnect.
func (f *SSEFrame) IsHeartbeat() bool {
	return f.EventType == nil || *f.EventType == ""
}
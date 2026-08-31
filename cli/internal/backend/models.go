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
	ProjectID      string   `json:"project_id"`
	Title          *string  `json:"title,omitempty"`
	ModelID        *string  `json:"model_id,omitempty"`
	ProviderID     *string  `json:"provider_id,omitempty"`
	RuntimeID      *string  `json:"runtime_id,omitempty"`
	PermissionMode *string  `json:"permission_mode,omitempty"`
	AgentSlug      *string  `json:"agent_slug,omitempty"`
	MCPSlugs       []string `json:"mcp_provider_slugs,omitempty"`
}

// SessionSkillsRequest mirrors PUT /v1/sessions/{id}/skills.
type SessionSkillsRequest struct {
	SkillIDs []string `json:"skill_ids"`
}

// SessionSkillsResponse mirrors the skills endpoint response.
type SessionSkillsResponse struct {
	SkillIDs []string `json:"skill_ids"`
}

// SessionMessageRequest mirrors POST /v1/sessions/{id}/messages.
type SessionMessageRequest struct {
	Prompt string `json:"prompt"`
}

// SessionListItem mirrors a row of GET /v1/sessions.
type SessionListItem struct {
	ID             string  `json:"id"`
	ProjectID      string  `json:"project_id"`
	Name           string  `json:"name"`
	Status         string  `json:"status"`
	UpdatedAt      *int64  `json:"updated_at"`
	Runtime        string  `json:"runtime_provider"`
	PermissionMode string  `json:"permission_mode"`
	AgentSlug      *string `json:"agent_slug"`
	ModelID        *string `json:"locked_model_id"`
}

// SessionListResponse mirrors GET /v1/sessions.
type SessionListResponse struct {
	Sessions []SessionListItem `json:"sessions"`
}

// SessionDetail mirrors GET/POST /v1/sessions (subset used by the CLI).
type SessionDetail struct {
	ID             string  `json:"id"`
	ProjectID      string  `json:"project_id"`
	Status         string  `json:"status"`
	Runtime        string  `json:"runtime_provider"`
	PermissionMode string  `json:"permission_mode"`
	AgentSlug      *string `json:"agent_slug"`
	ModelID        *string `json:"locked_model_id"`
}

// ── Task DTOs (mirrors of /v1/tasks) ────────────────────────────────

// KickoffTaskRequest mirrors POST /v1/projects/{id}/tasks.
type KickoffTaskRequest struct {
	Goal          string   `json:"goal"`
	LeadAgentSlug string   `json:"lead_agent_slug"`
	Refs          []string `json:"refs,omitempty"`
	Title         string   `json:"title,omitempty"`
	Worktree      bool     `json:"worktree,omitempty"`
}

// Task mirrors the Task schema (subset used by the CLI).
type Task struct {
	ID            string `json:"id"`
	ProjectID     string `json:"project_id"`
	Title         string `json:"title"`
	Goal          string `json:"goal"`
	Status        string `json:"status"`
	LeadAgentSlug string `json:"lead_agent_slug"`
	CurrentHolder string `json:"current_holder"`
}

// TaskRun mirrors one run row of a task.
type TaskRun struct {
	ID        string `json:"id"`
	SessionID string `json:"session_id"`
	AgentSlug string `json:"agent_slug"`
	Sequence  int    `json:"sequence"`
	Kind      string `json:"kind"`
	Status    string `json:"status"`
}

// TaskEvent mirrors one task event row.
type TaskEvent struct {
	ID        string `json:"id"`
	Sequence  int    `json:"sequence"`
	Type      string `json:"type"`
	Actor     string `json:"actor"`
	SessionID string `json:"session_id"`
	CreatedAt int64  `json:"created_at"`
}

// TaskDetail mirrors GET /v1/tasks/{id}.
type TaskDetail struct {
	Task   Task        `json:"task"`
	Runs   []TaskRun   `json:"runs"`
	Events []TaskEvent `json:"events"`
}

// TaskListResponse mirrors GET /v1/tasks.
type TaskListResponse struct {
	Tasks []Task `json:"tasks"`
}

// TaskInterveneRequest mirrors POST /v1/tasks/{id}:intervene.
type TaskInterveneRequest struct {
	Action string `json:"action"`
	Goal   string `json:"goal,omitempty"`
	Text   string `json:"text,omitempty"`
}

// ── Session actions (approval decisions) ────────────────────────────

// SessionActionRequest mirrors POST /v1/sessions/{id}/actions.
type SessionActionRequest struct {
	PendingID string `json:"pending_id"`
	Decision  string `json:"decision"` // approve|approve_with_changes|approve_for_session|reject|answer
	Message   string `json:"message,omitempty"`
}

// ── Runs (activity overview) ────────────────────────────────────────

// RunSummary mirrors one row of GET /v1/runs.
type RunSummary struct {
	SessionID   string  `json:"session_id"`
	SourceKind  string  `json:"source_kind"`
	ProjectID   string  `json:"project_id"`
	Title       string  `json:"title"`
	Status      string  `json:"status"`
	UpdatedAt   int64   `json:"updated_at"`
	ProjectName *string `json:"project_name"`
	TaskID      *string `json:"task_id"`
	Origin      string  `json:"origin"`
	LastMessage *string `json:"last_message"`
	LastOutput  *string `json:"last_output"`
	Model       *string `json:"model"`
	Runtime     *string `json:"runtime"`
}

// RunListResponse mirrors GET /v1/runs.
type RunListResponse struct {
	Runs []RunSummary `json:"runs"`
}

// ── Resources (agents / skills / connectors) ────────────────────────

// Agent mirrors AgentResponse (subset used by the CLI).
type Agent struct {
	ID             string   `json:"id"`
	Slug           string   `json:"slug"`
	Name           string   `json:"name"`
	Description    string   `json:"description"`
	Runtime        string   `json:"runtime"`
	Model          string   `json:"model"`
	Skills         []string `json:"skills"`
	ConnectorTypes []string `json:"connector_types"`
	PermissionMode string   `json:"permission_mode"`
	Source         string   `json:"source"`
	Deletable      bool     `json:"deletable"`
}

// AgentListResponse mirrors GET /v1/agents.
type AgentListResponse struct {
	Agents []Agent `json:"agents"`
}

// Skill mirrors a skill list item (GET /v1/skills).
type Skill struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Slug        string `json:"slug"`
	Description string `json:"description"`
	Category    string `json:"category"`
	Enabled     bool   `json:"enabled"`
}

// SkillListResponse mirrors GET /v1/skills.
type SkillListResponse struct {
	Skills []Skill `json:"skills"`
}

// Connector mirrors a connector list item (GET /v1/connectors).
type Connector struct {
	ID            string `json:"id"`
	Slug          string `json:"slug"`
	Name          string `json:"name"`
	Description   string `json:"description"`
	ConnectorType string `json:"connector_type"`
	Status        string `json:"status"`
	Enabled       bool   `json:"enabled"`
}

// ConnectorListResponse mirrors GET /v1/connectors.
type ConnectorListResponse struct {
	Connectors []Connector `json:"connectors"`
}

// TaskPlan mirrors GET /v1/tasks/{id}/plan.
type TaskPlan struct {
	Subtasks       []any `json:"subtasks"`
	Ready          bool  `json:"ready"`
	CurrentVersion int64 `json:"current_version"`
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

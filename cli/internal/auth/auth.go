// Package auth implements the CLI's login state: token storage (0600,
// never in profiles), the control-plane login/refresh/revoke client and
// the automatic refresh path every command uses to build its bearer.
//
// Boundary notes (design.md §7):
//   - Long-lived tokens never go into profile JSON or logs.
//   - `valuz auth login` is a human-local command; managed execution
//     contexts must use execution-scoped credentials and are forbidden
//     from falling back to this store (fail-closed).
package auth

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"time"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
)

// Control-plane identity endpoints (mounted under the cloud prefix; the
// server exposes them at /v1/auth/*, see services/valuz-server
// app/api/identity.py).
const (
	loginEndpoint   = "/v1/auth/login"
	refreshEndpoint = "/v1/auth/refresh"
	revokeEndpoint  = "/v1/auth/revoke"
	apiKeyEndpoint  = "/v1/auth/token"
	meEndpoint      = "/v1/auth/me"
)

// Principal is the token's identity summary.
type Principal struct {
	MasterID     string `json:"master_id"`
	Distribution string `json:"distribution"`
	OrgID        string `json:"org_id,omitempty"`
	OrgName      string `json:"org_name,omitempty"`
	Role         string `json:"role,omitempty"`
}

// TokenPair mirrors the control-plane TokenPair response.
type TokenPair struct {
	AccessToken  string    `json:"access_token"`
	RefreshToken string    `json:"refresh_token"`
	ExpiresIn    int       `json:"expires_in"`
	ExpiresAt    time.Time `json:"expires_at"`
	Email        string    `json:"email,omitempty"`
	Phone        string    `json:"phone,omitempty"`
	DisplayName  string    `json:"display_name,omitempty"`
	Principal    Principal `json:"principal"`
}

// LoginRequest mirrors POST {cloud}/identity/login.
type LoginRequest struct {
	Email    string `json:"email"`
	Password string `json:"password"`
	ClientID string `json:"client_id"`
	Resource string `json:"resource,omitempty"`
}

// ApiKeyRequest mirrors POST {cloud}/identity/token (vzp_ key exchange).
type ApiKeyRequest struct {
	APIKey string `json:"api_key"`
}

// Store persists the CLI's login state. The file is owner-only; contents
// are never rendered (redaction applies at the error/event layers).
type Store struct {
	Path string
}

// NewStore returns a store rooted at ~/.valuz-oss.
func NewStore() *Store {
	home, err := os.UserHomeDir()
	if err != nil {
		return &Store{Path: ""}
	}
	return &Store{Path: filepath.Join(home, ".valuz-oss", "auth.json")}
}

// Load reads the stored pair (nil when absent or unreadable).
func (s *Store) Load() (*TokenPair, error) {
	if s.Path == "" {
		return nil, nil
	}
	data, err := os.ReadFile(s.Path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, errs.Wrap(errs.KindInternal, err, "read auth state")
	}
	var pair TokenPair
	if err := json.Unmarshal(data, &pair); err != nil {
		return nil, errs.Wrap(errs.KindInternal, err, "parse auth state")
	}
	return &pair, nil
}

// Save writes the pair atomically with owner-only permissions.
func (s *Store) Save(pair *TokenPair) error {
	if s.Path == "" {
		return errors.New("no home directory for auth state")
	}
	if err := os.MkdirAll(filepath.Dir(s.Path), 0o700); err != nil {
		return errs.Wrap(errs.KindInternal, err, "create auth dir")
	}
	data, err := json.MarshalIndent(pair, "", "  ")
	if err != nil {
		return errs.Wrap(errs.KindInternal, err, "encode auth state")
	}
	tmp := s.Path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return errs.Wrap(errs.KindInternal, err, "write auth state")
	}
	if err := os.Rename(tmp, s.Path); err != nil {
		_ = os.Remove(tmp)
		return errs.Wrap(errs.KindInternal, err, "commit auth state")
	}
	return nil
}

// Clear removes the stored state.
func (s *Store) Clear() error {
	if s.Path == "" {
		return nil
	}
	if err := os.Remove(s.Path); err != nil && !errors.Is(err, os.ErrNotExist) {
		return errs.Wrap(errs.KindInternal, err, "remove auth state")
	}
	return nil
}

// Client talks to the control-plane identity endpoints. The control
// client's base URL already carries the /cloud prefix; endpoint paths are
// appended once.
type Client struct {
	HTTP *backend.ControlClient
}

// NewClient builds an identity client against cloudBaseURL.
func NewClient(cloudBaseURL string) *Client {
	return &Client{HTTP: backend.NewControlClient(cloudBaseURL, "")}
}

// Login exchanges email+password for a token pair.
func (c *Client) Login(email, password, clientID, resource string) (*TokenPair, error) {
	req := LoginRequest{Email: email, Password: password, ClientID: clientID, Resource: resource}
	var pair tokenPairWire
	if err := c.HTTP.Post(context.Background(), loginEndpoint, req, &pair); err != nil {
		return nil, err
	}
	return pair.toPair(), nil
}

// LoginWithAPIKey exchanges a vzp_ personal key for a token pair.
func (c *Client) LoginWithAPIKey(apiKey string) (*TokenPair, error) {
	var pair tokenPairWire
	if err := c.HTTP.Post(context.Background(), apiKeyEndpoint, ApiKeyRequest{APIKey: apiKey}, &pair); err != nil {
		return nil, err
	}
	return pair.toPair(), nil
}

// Refresh renews an expired access token.
func (c *Client) Refresh(refreshToken string) (*TokenPair, error) {
	req := map[string]string{"refresh_token": refreshToken}
	var pair tokenPairWire
	if err := c.HTTP.Post(context.Background(), refreshEndpoint, req, &pair); err != nil {
		return nil, err
	}
	return pair.toPair(), nil
}

// Revoke invalidates the stored refresh token.
func (c *Client) Revoke(refreshToken string) error {
	req := map[string]string{"refresh_token": refreshToken}
	return c.HTTP.Post(context.Background(), revokeEndpoint, req, nil)
}

// tokenPairWire mirrors the wire shape with a numeric expires_in.
type tokenPairWire struct {
	AccessToken  string    `json:"access_token"`
	RefreshToken string    `json:"refresh_token"`
	ExpiresIn    int       `json:"expires_in"`
	Email        string    `json:"email,omitempty"`
	Phone        string    `json:"phone,omitempty"`
	DisplayName  string    `json:"display_name,omitempty"`
	Principal    Principal `json:"principal"`
}

func (w tokenPairWire) toPair() *TokenPair {
	return &TokenPair{
		AccessToken:  w.AccessToken,
		RefreshToken: w.RefreshToken,
		ExpiresIn:    w.ExpiresIn,
		ExpiresAt:    time.Now().Add(time.Duration(w.ExpiresIn) * time.Second),
		Email:        w.Email,
		Phone:        w.Phone,
		DisplayName:  w.DisplayName,
		Principal:    w.Principal,
	}
}

// Expired reports whether the access token is at (or near) expiry.
func (p *TokenPair) Expired() bool {
	if p == nil || p.AccessToken == "" {
		return true
	}
	// Refresh 30s before actual expiry to avoid mid-run 401s.
	return time.Now().After(p.ExpiresAt.Add(-30 * time.Second))
}

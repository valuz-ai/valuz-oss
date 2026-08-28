// Package config implements the product CLI shell's non-sensitive
// configuration: named profiles, the unified resolver precedence
// (flag > execution-scoped env > named profile > discovery/default) and
// profile persistence with safe file semantics.
//
// Credentials are deliberately NOT part of this package: token resolution
// lives in the auth path (Slice 5) and never flows through the generic
// override order.
package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

// ProfileDir is the directory holding named profiles. Kept next to the
// existing ~/.valuz-oss/logs bundle convention.
func ProfileDir() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return filepath.Join(home, ".valuz-oss", "profiles")
}

// Profile is a named set of non-sensitive connection defaults. It never
// holds tokens: access/refresh credentials live in the secure auth store
// (Slice 5), referenced by name, not inlined here.
type Profile struct {
	// Name is the profile's file stem; not serialized.
	Name string `json:"-"`
	// BackendURL pins the backend base URL for this profile. Empty falls
	// through to env/discovery.
	BackendURL string `json:"backend_url,omitempty"`
	// Defaults carries non-sensitive run defaults (e.g. VALUZ_RUN_* values).
	// Keys use the canonical env names without the VALUZ_ prefix.
	Defaults map[string]string `json:"defaults,omitempty"`
}

// ProfileStore reads and writes named profiles.
type ProfileStore struct {
	Dir string
}

// NewProfileStore returns a store rooted at dir (defaults to ProfileDir()).
func NewProfileStore(dir string) *ProfileStore {
	if dir == "" {
		dir = ProfileDir()
	}
	return &ProfileStore{Dir: dir}
}

// Load reads a profile by name. A missing file yields a zero Profile with
// the name set (never an error), so the default profile is always usable.
func (s *ProfileStore) Load(name string) (*Profile, error) {
	p := &Profile{Name: name}
	if name == "" {
		return p, nil
	}
	path := s.path(name)
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return p, nil
	}
	if err != nil {
		return nil, fmt.Errorf("read profile %q: %w", name, err)
	}
	if err := json.Unmarshal(data, p); err != nil {
		return nil, fmt.Errorf("parse profile %q: %w", name, err)
	}
	p.Name = name
	return p, nil
}

// Save writes the profile atomically (temp + rename) with owner-only
// permissions. Profile contents are non-sensitive today, but 0600 is cheap
// insurance against accidentally staging a future credential field.
func (s *ProfileStore) Save(p *Profile) error {
	if p.Name == "" {
		return errors.New("profile name is required")
	}
	if err := os.MkdirAll(s.Dir, 0o700); err != nil {
		return fmt.Errorf("create profile dir: %w", err)
	}
	data, err := json.MarshalIndent(p, "", "  ")
	if err != nil {
		return fmt.Errorf("encode profile: %w", err)
	}
	tmp := s.path(p.Name) + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return fmt.Errorf("write profile temp: %w", err)
	}
	if err := os.Rename(tmp, s.path(p.Name)); err != nil {
		_ = os.Remove(tmp)
		return fmt.Errorf("commit profile: %w", err)
	}
	return nil
}

func (s *ProfileStore) path(name string) string {
	return filepath.Join(s.Dir, name+".json")
}

// Default returns the value of key (canonical name without VALUZ_ prefix)
// from the profile, or "" when absent.
func (p *Profile) Default(key string) string {
	if p == nil || p.Defaults == nil {
		return ""
	}
	return p.Defaults[key]
}
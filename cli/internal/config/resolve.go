package config

import (
	"fmt"
	"os"
	"strings"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/runtime"
)

// Resolver implements the non-sensitive configuration precedence:
//
//	flag > execution-scoped env > named profile > discovery/default
//
// Credentials (bearer tokens) use a separate resolver and are never read
// through this generic path.
type Resolver struct {
	// Profile is the resolved named profile (may be a zero profile).
	Profile *Profile
	// Discovery returns the runtime paths used for the backend URL
	// fallback. Nil falls back to runtime.Discover().
	Discovery func() (*runtime.Paths, error)
	// LookupEnv is the environment source; defaults to os.LookupEnv.
	LookupEnv func(string) (string, bool)
}

// NewResolver builds a resolver with sane defaults.
func NewResolver(profile *Profile) *Resolver {
	if profile == nil {
		profile = &Profile{}
	}
	return &Resolver{
		Profile:   profile,
		Discovery: runtime.Discover,
		LookupEnv: os.LookupEnv,
	}
}

// BackendURL resolves the backend base URL with the canonical precedence:
// explicit flag value (non-empty) > env VALUZ_BACKEND_BASE_URL > named
// profile > runtime discovery > localhost dev default.
func (r *Resolver) BackendURL(flagVal string) (string, error) {
	if flagVal != "" {
		return strings.TrimRight(flagVal, "/"), nil
	}
	if v, ok := r.LookupEnv("VALUZ_BACKEND_BASE_URL"); ok && v != "" {
		return strings.TrimRight(v, "/"), nil
	}
	if r.Profile != nil && r.Profile.BackendURL != "" {
		return strings.TrimRight(r.Profile.BackendURL, "/"), nil
	}
	if r.Discovery != nil {
		if paths, err := r.Discovery(); err == nil && paths.BackendPort > 0 {
			return fmt.Sprintf("http://127.0.0.1:%d", paths.BackendPort), nil
		}
	}
	return "http://127.0.0.1:8000", nil
}

// String resolves a generic non-sensitive setting:
// flag > env(VALUZ_<key>) > profile default > fallback.
func (r *Resolver) String(key, flagVal, fallback string) string {
	if flagVal != "" {
		return flagVal
	}
	envKey := "VALUZ_" + key
	if v, ok := r.LookupEnv(envKey); ok && v != "" {
		return v
	}
	if v := r.Profile.Default(key); v != "" {
		return v
	}
	return fallback
}
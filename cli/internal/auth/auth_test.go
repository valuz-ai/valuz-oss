package auth

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestStoreRoundTripAndPermissions(t *testing.T) {
	dir := t.TempDir()
	store := &Store{Path: filepath.Join(dir, "auth.json")}

	pair := &TokenPair{
		AccessToken:  "acc-123",
		RefreshToken: "ref-456",
		ExpiresIn:    3600,
		ExpiresAt:    time.Now().Add(time.Hour),
		Email:        "dev@valuz.ai",
		Principal:    Principal{MasterID: "m-1", Distribution: "fin", OrgName: "QA"},
	}
	if err := store.Save(pair); err != nil {
		t.Fatalf("Save: %v", err)
	}

	info, err := os.Stat(store.Path)
	if err != nil {
		t.Fatalf("stat: %v", err)
	}
	if perm := info.Mode().Perm(); perm != 0o600 {
		t.Fatalf("auth file perm = %o, want 600", perm)
	}

	got, err := store.Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if got.AccessToken != "acc-123" || got.Principal.OrgName != "QA" {
		t.Fatalf("round trip mismatch: %+v", got)
	}
	if got.Expired() {
		t.Fatal("fresh token must not be expired")
	}

	if err := store.Clear(); err != nil {
		t.Fatalf("Clear: %v", err)
	}
	missing, err := store.Load()
	if err != nil || missing != nil {
		t.Fatalf("cleared store should load nil: %v err=%v", missing, err)
	}
}

func TestTokenExpiry(t *testing.T) {
	pair := &TokenPair{AccessToken: "x", ExpiresAt: time.Now().Add(-time.Minute)}
	if !pair.Expired() {
		t.Fatal("past-expiry token must be expired")
	}
	pair.ExpiresAt = time.Now().Add(10 * time.Second)
	if !pair.Expired() {
		t.Fatal("near-expiry token must be considered expired (30s margin)")
	}
	pair.ExpiresAt = time.Now().Add(2 * time.Minute)
	if pair.Expired() {
		t.Fatal("valid token must not be expired")
	}
}
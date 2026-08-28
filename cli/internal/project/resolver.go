// Package project resolves the run's execution directory to a backend
// project id (design.md §4.2 rule 3): normalize cwd, look up by root_path,
// create when missing.
package project

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
)

// Resolver maps a local directory to a backend project.
type Resolver struct {
	Client *backend.ControlClient
}

// Resolve returns the project id for dir, creating the project when the
// backend has no row for the normalized root path.
func (r *Resolver) Resolve(ctx context.Context, dir string) (string, error) {
	abs, err := filepath.Abs(dir)
	if err != nil {
		return "", errs.Wrap(errs.KindUsage, err, "normalize cwd %q", dir)
	}
	abs = filepath.Clean(abs)

	var list backend.ProjectList
	if err := r.Client.Get(ctx, "/v1/projects", &list); err != nil {
		return "", err
	}
	for _, p := range list.Projects {
		if p.RootPath == abs {
			return p.ID, nil
		}
	}

	var created backend.Project
	body := backend.Project{Name: filepath.Base(abs), RootPath: abs}
	if err := r.Client.Post(ctx, "/v1/projects", body, &created); err != nil {
		return "", err
	}
	if created.ID == "" {
		return "", errors.New("backend returned a project without id")
	}
	return created.ID, nil
}

// ParseID is a small helper for flag validation (no-op pass-through).
func ParseID(v string) (string, error) {
	if v == "" {
		return "", fmt.Errorf("empty id")
	}
	return v, nil
}
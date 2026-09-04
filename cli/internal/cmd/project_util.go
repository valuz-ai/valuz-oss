package cmd

import (
	"context"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
	errs "code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/errors"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/project"
)

// resolveProjectWith resolves cwd to a project id (lookup-or-create).
func resolveProjectWith(client *backend.ControlClient, ctx context.Context, cwd string) (string, error) {
	pid, err := (&project.Resolver{Client: client}).Resolve(ctx, cwd)
	if err != nil {
		return "", err
	}
	return pid, nil
}

// idOrResolve accepts an explicit project id or resolves from cwd.
func idOrResolve(client *backend.ControlClient, ctx context.Context, id, cwd string) (string, error) {
	if id != "" {
		return id, nil
	}
	if cwd != "" {
		return resolveProjectWith(client, ctx, cwd)
	}
	return "", errs.New(errs.KindUsage, "provide --project <id> or --cwd <dir>")
}

// idOrQuick accepts an explicit project id, resolves from cwd, or falls
// back to the quick-chat project (product "快速对话" shape).
func idOrQuick(client *backend.ControlClient, ctx context.Context, id, cwd string) (string, error) {
	if id != "" {
		return id, nil
	}
	if cwd != "" {
		return resolveProjectWith(client, ctx, cwd)
	}
	return "chat-default", nil
}

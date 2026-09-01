package cmd

import (
	"fmt"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/backend"
)

// newActivityCmd builds `valuz activity ...` — the cross-project run
// overview (mobile "动态": needs-attention / running / recently-finished).
// One functional command over GET /v1/runs with status grouping.
func newActivityCmd() *cobra.Command {
	var (
		status    string
		projectID string
		limit     int
	)
	cmd := &cobra.Command{
		Use:   "activity",
		Short: "Show activity overview (running / finished runs)",
		RunE: func(cmd *cobra.Command, _ []string) error {
			opts, err := Options(cmd)
			if err != nil {
				return err
			}
			path := "/v1/runs?status=" + status + "&limit=" + fmt.Sprint(limit)
			if projectID != "" {
				path += "&project_id=" + projectID
			}
			token, err := resolveBearer(opts)
			if err != nil {
				return err
			}
			client := newControlClient(opts, token)
			var resp backend.RunListResponse
			if err := client.Get(cmd.Context(), path, &resp); err != nil {
				return err
			}
			if len(resp.Runs) == 0 {
				fmt.Fprintln(cmd.OutOrStdout(), "(no runs)")
				return nil
			}
			for _, r := range resp.Runs {
				last := r.LastMessage
				if last == nil {
					last = r.LastOutput
				}
				text := ""
				if last != nil {
					text = truncate(*last, 60)
				}
				project := ""
				if r.ProjectName != nil {
					project = *r.ProjectName
				}
				fmt.Fprintf(cmd.OutOrStdout(), "%-36s  %-8s  %-16s  %-40s  %s\n",
					r.SessionID, r.Status, project, truncate(r.Title, 40), text)
			}
			return nil
		},
	}
	f := cmd.Flags()
	f.StringVar(&status, "status", "running", "running|finished")
	f.StringVar(&projectID, "project", "", "filter by project id")
	f.IntVar(&limit, "limit", 50, "max runs per group")
	return cmd
}

// Command valuz is the user-facing Valuz product CLI.
//
// It owns runtime orchestration only — process startup, signals, ports, pid
// files, logs, path discovery, and doctor checks — and never duplicates the
// implementation of backend, webui, desktop, or tui hosts. See
// docs/STRUCTURE.md for the ownership model.
package main

import (
	"fmt"
	"os"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/cmd"
)

func main() {
	if err := cmd.Root().Execute(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

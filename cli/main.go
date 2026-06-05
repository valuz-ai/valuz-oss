package main

import (
	"fmt"
	"os"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/cmd"
)

// Set via -ldflags at build time: -X main.version=x.y.z
var version = "dev"

func main() {
	cmd.SetVersion(version)
	if err := cmd.Root().Execute(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

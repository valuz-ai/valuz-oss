package main

import (
	"os"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/cmd"
)

// Set via -ldflags at build time: -X main.version=x.y.z
var version = "dev"

func main() {
	cmd.SetVersion(version)
	os.Exit(cmd.Execute(os.Args[1:], os.Stdout, os.Stderr))
}

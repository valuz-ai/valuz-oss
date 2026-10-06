package pluginpkg

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// baseManifest is a valid manifest exercising most fields.
func baseManifest() map[string]any {
	return map[string]any{
		"manifestVersion": 1,
		"id":              "acme.hello",
		"version":         "1.2.0",
		"name":            map[string]any{"en-US": "Hello", "zh-CN": "你好"},
		"description":     "Says hello",
		"publisher":       map[string]any{"name": "Acme", "url": "https://acme.example"},
		"engines":         map[string]any{"valuz-plugin-api": "^1.0.0"},
		"requires":        []any{"edition:finance"},
		"frontend":        map[string]any{"entry": "frontend/index.js", "styles": []any{"frontend/index.css"}},
		"permissions":     []any{"storage", "notifications"},
		"config":          map[string]any{"type": "object", "properties": map[string]any{}},
		"automations": []any{
			map[string]any{
				"name":    "daily",
				"runtime": "python",
				"entry":   "automations/daily.py",
				"trigger": map[string]any{"cron": "0 9 * * 1-5"},
			},
			map[string]any{"name": "manual-run", "runtime": "shell", "entry": "automations/run.sh", "trigger": "manual"},
		},
		"icon":     "icon.png",
		"keywords": []any{"hello"},
	}
}

// baseFiles are the package files baseManifest references, plus the
// extras the pack file-set tests look for.
func baseFiles() map[string]string {
	return map[string]string{
		"frontend/index.js":    "export default definePlugin({ id: 'acme.hello' })\n",
		"frontend/index.css":   "a { color: red }\n",
		"locales/en-US.json":   `{"title": "Hello"}`,
		"locales/zh-CN.json":   `{"title": "你好"}`,
		"automations/daily.py": "print('hi')\n",
		"automations/run.sh":   "echo hi\n",
		"icon.png":             "png",
		"README.md":            "# Hello\n",
		"LICENSE":              "MIT\n",
	}
}

// writePlugin writes manifest + files under dir (manifest nil = none).
func writePlugin(t *testing.T, dir string, manifest map[string]any, files map[string]string) {
	t.Helper()
	if manifest != nil {
		raw, err := json.MarshalIndent(manifest, "", "  ")
		if err != nil {
			t.Fatal(err)
		}
		writeFile(t, filepath.Join(dir, ManifestFile), string(raw))
	}
	for name, content := range files {
		writeFile(t, filepath.Join(dir, filepath.FromSlash(name)), content)
	}
}

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

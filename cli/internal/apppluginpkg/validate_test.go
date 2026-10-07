package apppluginpkg

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestValidateRules(t *testing.T) {
	type tc struct {
		name     string
		mutate   func(m map[string]any, files map[string]string)
		raw      string // replaces the manifest file verbatim when set
		noFile   bool   // no manifest at all
		wantErrs []string
		wantWarn []string
	}
	frontend := func(m map[string]any) map[string]any { return m["frontend"].(map[string]any) }
	auto := func(m map[string]any, i int) map[string]any { return m["automations"].([]any)[i].(map[string]any) }
	cases := []tc{
		{name: "valid"},
		{name: "minimal", mutate: func(m map[string]any, _ map[string]string) {
			for _, k := range []string{"description", "requires", "permissions", "config", "automations", "icon", "keywords"} {
				delete(m, k)
			}
		}},
		{name: "no manifest", noFile: true, wantErrs: []string{"valuz-plugin.json not found"}},
		{name: "manifest not JSON", raw: "{nope", wantErrs: []string{"valuz-plugin.json: invalid JSON"}},
		{name: "manifest not an object", raw: "[]", wantErrs: []string{"must be a JSON object"}},
		{name: "required fields", mutate: func(m map[string]any, _ map[string]string) {
			delete(m, "id")
			delete(m, "engines")
			delete(m, "frontend")
		}, wantErrs: []string{"id: is required", "engines: is required", "frontend: is required"}},
		{name: "manifestVersion 2", mutate: func(m map[string]any, _ map[string]string) { m["manifestVersion"] = 2 },
			wantErrs: []string{"manifestVersion: must be 1"}},
		{name: "manifestVersion 1.0 equals 1", raw: strings.Replace(mustManifestJSON(t), `"manifestVersion": 1`, `"manifestVersion": 1.0`, 1)},
		{name: "unknown field", mutate: func(m map[string]any, _ map[string]string) { m["main"] = "index.js" },
			wantErrs: []string{"main: is not a known field"}},
		{name: "id uppercase", mutate: func(m map[string]any, _ map[string]string) { m["id"] = "Acme.Hello" },
			wantErrs: []string{`id: "Acme.Hello" does not match`}},
		{name: "id without publisher", mutate: func(m map[string]any, _ map[string]string) { m["id"] = "hello" },
			wantErrs: []string{`id: "hello" does not match`}},
		{name: "id reserved valuz.", mutate: func(m map[string]any, _ map[string]string) { m["id"] = "valuz.hello" },
			wantErrs: []string{`uses the reserved prefix "valuz."`}},
		{name: "id reserved oss-", mutate: func(m map[string]any, _ map[string]string) { m["id"] = "oss-tools.hello" },
			wantErrs: []string{`uses the reserved prefix "oss-"`}},
		{name: "version not semver", mutate: func(m map[string]any, _ map[string]string) { m["version"] = "1.2" },
			wantErrs: []string{`version: "1.2" does not match`}},
		{name: "version prerelease+build", mutate: func(m map[string]any, _ map[string]string) { m["version"] = "1.2.0-beta.1+sha.5" }},
		{name: "engines empty range", mutate: func(m map[string]any, _ map[string]string) {
			m["engines"] = map[string]any{"valuz-plugin-api": ""}
		}, wantErrs: []string{"engines.valuz-plugin-api: must not be empty"}},
		{name: "engines missing api", mutate: func(m map[string]any, _ map[string]string) { m["engines"] = map[string]any{} },
			wantErrs: []string{"engines.valuz-plugin-api: is required"}},
		{name: "engines excludes API 1", mutate: func(m map[string]any, _ map[string]string) {
			m["engines"] = map[string]any{"valuz-plugin-api": "^2.0.0"}
		}, wantWarn: []string{`"^2.0.0" does not include plugin API 1.0.0`}},
		{name: "requires pattern", mutate: func(m map[string]any, _ map[string]string) {
			m["requires"] = []any{"edition:finance", "plugin:other"}
		}, wantErrs: []string{`requires[1]: "plugin:other" does not match`}},
		{name: "requires duplicates", mutate: func(m map[string]any, _ map[string]string) {
			m["requires"] = []any{"edition:finance", "edition:finance"}
		}, wantErrs: []string{"requires: must not contain duplicates"}},
		{name: "permissions enum", mutate: func(m map[string]any, _ map[string]string) {
			m["permissions"] = []any{"storage", "filesystem"}
		}, wantErrs: []string{`permissions[1]: "filesystem" is not one of`}},
		{name: "name empty string", mutate: func(m map[string]any, _ map[string]string) { m["name"] = "" },
			wantErrs: []string{"name: must not be empty"}},
		{name: "name empty object", mutate: func(m map[string]any, _ map[string]string) { m["name"] = map[string]any{} },
			wantErrs: []string{"name: must have at least 1 entry"}},
		{name: "name bad language key", mutate: func(m map[string]any, _ map[string]string) {
			m["name"] = map[string]any{"english": "Hello"}
		}, wantErrs: []string{`name: key "english"`}},
		{name: "name wrong type", mutate: func(m map[string]any, _ map[string]string) { m["name"] = 5 },
			wantErrs: []string{"name: must be a string or an object"}},
		{name: "publisher without name", mutate: func(m map[string]any, _ map[string]string) {
			m["publisher"] = map[string]any{"url": "x", "team": "y"}
		}, wantErrs: []string{"publisher.name: is required", "publisher.team: is not a known field"}},
		{name: "entry escapes with ..", mutate: func(m map[string]any, _ map[string]string) {
			frontend(m)["entry"] = "frontend/../../evil.js"
		}, wantErrs: []string{`frontend.entry: "frontend/../../evil.js" must not contain a '..' segment`}},
		{name: "entry absolute", mutate: func(m map[string]any, _ map[string]string) { frontend(m)["entry"] = "/etc/x.js" },
			wantErrs: []string{`frontend.entry: "/etc/x.js" does not match`}},
		{name: "entry missing", mutate: func(m map[string]any, files map[string]string) { delete(files, "frontend/index.js") },
			wantErrs: []string{`frontend.entry: "frontend/index.js" does not exist in the package`}},
		{name: "entry not an ES module", mutate: func(m map[string]any, files map[string]string) {
			frontend(m)["entry"] = "frontend/index.ts"
			files["frontend/index.ts"] = "export default 1"
		}, wantErrs: []string{"must be an ES module"}},
		{name: "entry without export", mutate: func(m map[string]any, files map[string]string) {
			files["frontend/index.js"] = "console.log(1)"
		}, wantWarn: []string{"has no export"}},
		{name: "style missing", mutate: func(m map[string]any, files map[string]string) { delete(files, "frontend/index.css") },
			wantErrs: []string{`frontend.styles[0]: "frontend/index.css" does not exist`}},
		{name: "backend is B-level", mutate: func(m map[string]any, _ map[string]string) {
			m["backend"] = map[string]any{"runtime": "node", "entry": "backend/server.mjs"}
		}, wantErrs: []string{"backend: B-level plugins are not supported by plugin API 1.x"}},
		{name: "automation names unique", mutate: func(m map[string]any, _ map[string]string) { auto(m, 1)["name"] = "daily" },
			wantErrs: []string{`automations[1].name: "daily" is used by more than one automation`}},
		{name: "automation name pattern", mutate: func(m map[string]any, _ map[string]string) { auto(m, 0)["name"] = "Daily_Run" },
			wantErrs: []string{`automations[0].name: "Daily_Run" does not match`}},
		{name: "automation runtime enum", mutate: func(m map[string]any, _ map[string]string) { auto(m, 0)["runtime"] = "node" },
			wantErrs: []string{`automations[0].runtime: "node" is not one of: "python", "shell"`}},
		{name: "automation entry missing", mutate: func(m map[string]any, files map[string]string) {
			delete(files, "automations/daily.py")
		}, wantErrs: []string{`automations[0].entry: "automations/daily.py" does not exist`}},
		{name: "automation trigger word", mutate: func(m map[string]any, _ map[string]string) { auto(m, 0)["trigger"] = "daily" },
			wantErrs: []string{`automations[0].trigger: must be "manual"`}},
		{name: "automation trigger interval too short", mutate: func(m map[string]any, _ map[string]string) {
			auto(m, 0)["trigger"] = map[string]any{"intervalSec": 30}
		}, wantErrs: []string{"automations[0].trigger.intervalSec: must be at least 60"}},
		{name: "automation timeout integer", mutate: func(m map[string]any, _ map[string]string) { auto(m, 0)["timeoutSec"] = 1.5 },
			wantErrs: []string{"automations[0].timeoutSec: must be an integer"}},
		{name: "automation input schema type", mutate: func(m map[string]any, _ map[string]string) {
			auto(m, 0)["input"] = map[string]any{"type": "string"}
		}, wantErrs: []string{`automations[0].input: must be a JSON Schema whose type is "object"`}},
		{name: "too many automations", mutate: func(m map[string]any, files map[string]string) {
			list := []any{}
			for i := 0; i < 21; i++ {
				list = append(list, map[string]any{"name": "a" + string(rune('a'+i)), "runtime": "shell", "entry": "automations/run.sh"})
			}
			m["automations"] = list
		}, wantErrs: []string{"automations: must have at most 20 items"}},
		{name: "config not object schema", mutate: func(m map[string]any, _ map[string]string) {
			m["config"] = map[string]any{"type": "array"}
		}, wantErrs: []string{`config: must be a JSON Schema whose type is "object"`}},
		{name: "locale invalid JSON", mutate: func(m map[string]any, files map[string]string) {
			files["locales/zh-CN.json"] = "{broken"
		}, wantErrs: []string{"locales/zh-CN.json: invalid JSON"}},
		{name: "custom locales dir missing", mutate: func(m map[string]any, _ map[string]string) { m["locales"] = "i18n" },
			wantWarn: []string{`locales: "i18n" does not exist in the package`}},
		{name: "icon missing", mutate: func(m map[string]any, files map[string]string) { delete(files, "icon.png") },
			wantWarn: []string{`icon: "icon.png" does not exist in the package`}},
		{name: "keywords too many", mutate: func(m map[string]any, _ map[string]string) {
			kw := []any{}
			for i := 0; i < 21; i++ {
				kw = append(kw, "k")
			}
			m["keywords"] = kw
		}, wantErrs: []string{"keywords: must have at most 20 items"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			dir := t.TempDir()
			m, files := baseManifest(), baseFiles()
			if c.mutate != nil {
				c.mutate(m, files)
			}
			switch {
			case c.noFile:
				writePlugin(t, dir, nil, files)
			case c.raw != "":
				writePlugin(t, dir, nil, files)
				writeFile(t, filepath.Join(dir, ManifestFile), c.raw)
			default:
				writePlugin(t, dir, m, files)
			}
			res, err := ValidateDir(dir)
			if err != nil {
				t.Fatal(err)
			}
			if len(c.wantErrs) == 0 && !res.OK {
				t.Fatalf("want ok, got errors %q", res.Errors)
			}
			if len(c.wantErrs) > 0 && res.OK {
				t.Fatalf("want errors %q, got ok", c.wantErrs)
			}
			for _, want := range c.wantErrs {
				if !containsSub(res.Errors, want) {
					t.Errorf("missing error %q in %q", want, res.Errors)
				}
			}
			for _, want := range c.wantWarn {
				if !containsSub(res.Warnings, want) {
					t.Errorf("missing warning %q in %q", want, res.Warnings)
				}
			}
		})
	}
}

func TestValidateDirRejectsSymlinkEscape(t *testing.T) {
	dir, outside := t.TempDir(), t.TempDir()
	writePlugin(t, dir, baseManifest(), baseFiles())
	writeFile(t, filepath.Join(outside, "evil.js"), "export default 1")
	entry := filepath.Join(dir, "frontend", "index.js")
	if err := os.Remove(entry); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(outside, "evil.js"), entry); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	res, err := ValidateDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if !containsSub(res.Errors, `frontend.entry: "frontend/index.js" resolves outside the package`) {
		t.Fatalf("errors = %q", res.Errors)
	}
}

func TestValidateResultCarriesManifest(t *testing.T) {
	dir := t.TempDir()
	writePlugin(t, dir, baseManifest(), baseFiles())
	res, err := ValidateDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if res.ID() != "acme.hello" || res.Version() != "1.2.0" || res.Errors == nil || res.Warnings == nil {
		t.Fatalf("unexpected result %+v", res)
	}
	if LocalizedText(res.Manifest["name"]) != "Hello" {
		t.Fatalf("name = %q", LocalizedText(res.Manifest["name"]))
	}
}

func mustManifestJSON(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	writePlugin(t, dir, baseManifest(), nil)
	raw, err := os.ReadFile(filepath.Join(dir, ManifestFile))
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func containsSub(list []string, sub string) bool {
	for _, s := range list {
		if strings.Contains(s, sub) {
			return true
		}
	}
	return false
}

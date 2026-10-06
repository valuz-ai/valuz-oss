package pluginpkg

import (
	"archive/zip"
	"bytes"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

// ManifestFile is the manifest's file name at the package root.
const ManifestFile = "valuz-plugin.json"

// DefaultLocalesDir is the locales directory when the manifest names none.
const DefaultLocalesDir = "locales"

// ErrBackendUnsupported is the error text for a manifest declaring a B-level
// backend (x-valuz-rules: plugin API 1.x has none).
const ErrBackendUnsupported = "B-level plugins are not supported by plugin API 1.x"

// Result mirrors the backend's validate() shape: {"ok","errors","warnings","manifest"}.
type Result struct {
	OK       bool           `json:"ok"`
	Errors   []string       `json:"errors"`
	Warnings []string       `json:"warnings"`
	Manifest map[string]any `json:"manifest"`
}

// ID returns manifest.id ("" when absent).
func (r *Result) ID() string { return stringField(r.Manifest, "id") }

// Version returns manifest.version ("" when absent).
func (r *Result) Version() string { return stringField(r.Manifest, "version") }

func (r *Result) errorf(format string, args ...any) {
	r.Errors = append(r.Errors, fmt.Sprintf(format, args...))
}

func (r *Result) warnf(format string, args ...any) {
	r.Warnings = append(r.Warnings, fmt.Sprintf(format, args...))
}

func (r *Result) finish() *Result {
	if r.Errors == nil {
		r.Errors = []string{}
	}
	if r.Warnings == nil {
		r.Warnings = []string{}
	}
	r.OK = len(r.Errors) == 0
	return r
}

// ValidateDir validates a plugin source directory.
func ValidateDir(dir string) (*Result, error) {
	abs, err := filepath.Abs(dir)
	if err != nil {
		return nil, err
	}
	info, err := os.Stat(abs)
	if err != nil {
		return nil, err
	}
	if !info.IsDir() {
		return nil, fmt.Errorf("%s is not a directory", dir)
	}
	r := validateFS(os.DirFS(abs))
	if r.Manifest != nil {
		checkSymlinkEscapes(abs, r)
		checkPackedSet(abs, r)
	}
	return r.finish(), nil
}

// checkPackedSet warns about manifest files that exist in the directory but
// lie outside the file set `pack` ships (pack then refuses).
func checkPackedSet(dir string, r *Result) {
	files, err := FileSet(dir, r.Manifest, "")
	if err != nil {
		return
	}
	packed := make(map[string]bool, len(files))
	for _, f := range files {
		packed[f] = true
	}
	for _, ref := range manifestPaths(r.Manifest) {
		if ref.field == "locales" || hasDotDot(ref.value) {
			continue
		}
		clean := path.Clean(ref.value)
		if info, err := os.Stat(filepath.Join(dir, filepath.FromSlash(clean))); err != nil || !info.Mode().IsRegular() {
			continue
		}
		if !packed[clean] {
			r.warnf("%s: %q is outside the packed file set (frontend/, the locales directory, automations/, the icon, README.md, LICENSE*); `pack` will refuse it", ref.field, ref.value)
		}
	}
}

// ValidateZip validates a packed plugin zip.
func ValidateZip(zipPath string) (*Result, error) {
	data, err := os.ReadFile(zipPath)
	if err != nil {
		return nil, err
	}
	return ValidateZipBytes(data)
}

// ValidateZipBytes validates a packed plugin zip held in memory.
func ValidateZipBytes(data []byte) (*Result, error) {
	zr, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil && !errors.Is(err, zip.ErrInsecurePath) {
		return nil, fmt.Errorf("not a zip archive: %w", err)
	}
	r := &Result{}
	for _, f := range zr.File {
		if !fs.ValidPath(strings.TrimSuffix(f.Name, "/")) || strings.HasPrefix(f.Name, "/") {
			r.errorf("zip entry %q is not a relative path inside the package", f.Name)
		}
	}
	if len(r.Errors) > 0 {
		return r.finish(), nil
	}
	return validateFS(zr).finish(), nil
}

// validateFS applies the schema and the x-valuz-rules to the package in fsys.
func validateFS(fsys fs.FS) *Result {
	r := &Result{}
	raw, err := fs.ReadFile(fsys, ManifestFile)
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			r.errorf("%s not found at the package root", ManifestFile)
		} else {
			r.errorf("read %s: %v", ManifestFile, err)
		}
		return r
	}
	doc, err := decodeJSON(raw)
	if err != nil {
		r.errorf("%s: invalid JSON: %v", ManifestFile, err)
		return r
	}
	manifest, ok := doc.(map[string]any)
	if !ok {
		r.errorf("%s: must be a JSON object", ManifestFile)
		return r
	}
	r.Manifest = manifest

	root, err := loadSchema()
	if err != nil {
		r.errorf("%v", err)
		return r
	}
	for _, is := range newEvaluator(root).eval(root, manifest, "") {
		r.Errors = append(r.Errors, is.String())
	}
	applyRules(fsys, manifest, r)
	return r
}

// applyRules implements the schema's x-valuz-rules plus the package checks
// (files exist, locales parse, engines range covers the plugin API).
func applyRules(fsys fs.FS, m map[string]any, r *Result) {
	// id must not start with a reserved prefix.
	if id, ok := m["id"].(string); ok {
		for _, prefix := range ReservedPrefixes() {
			if strings.HasPrefix(id, prefix) {
				r.errorf("id: %q uses the reserved prefix %q", id, prefix)
				break
			}
		}
	}

	// backend (B-level) is not supported by plugin API 1.x.
	if _, ok := m["backend"]; ok {
		r.errorf("backend: %s", ErrBackendUnsupported)
	}

	// Relative paths: no '..' segment, inside the package.
	for _, ref := range manifestPaths(m) {
		if hasDotDot(ref.value) {
			r.errorf("%s: %q must not contain a '..' segment", ref.field, ref.value)
		}
	}

	// frontend.entry and frontend.styles[] must exist; entry must be an ES module.
	if fe, ok := m["frontend"].(map[string]any); ok {
		if entry, ok := fe["entry"].(string); ok && !hasDotDot(entry) {
			if checkFile(fsys, "frontend.entry", entry, r) {
				ext := strings.ToLower(path.Ext(entry))
				if ext != ".js" && ext != ".mjs" {
					r.errorf("frontend.entry: %q must be an ES module (.js or .mjs)", entry)
				} else if data, err := fs.ReadFile(fsys, entry); err == nil && !bytes.Contains(data, []byte("export")) {
					r.warnf("frontend.entry: %q has no export; it must default-export definePlugin(...)", entry)
				}
			}
		}
		if styles, ok := fe["styles"].([]any); ok {
			for i, s := range styles {
				if p, ok := s.(string); ok && !hasDotDot(p) {
					checkFile(fsys, fmt.Sprintf("frontend.styles[%d]", i), p, r)
				}
			}
		}
	}

	// automations: unique names; entries exist; input is an object schema.
	if autos, ok := m["automations"].([]any); ok {
		seen := map[string]bool{}
		for i, a := range autos {
			am, ok := a.(map[string]any)
			if !ok {
				continue
			}
			field := fmt.Sprintf("automations[%d]", i)
			if name, ok := am["name"].(string); ok {
				if seen[name] {
					r.errorf("%s.name: %q is used by more than one automation", field, name)
				}
				seen[name] = true
			}
			if entry, ok := am["entry"].(string); ok && !hasDotDot(entry) {
				checkFile(fsys, field+".entry", entry, r)
			}
			if input, ok := am["input"]; ok && !isObjectSchema(input) {
				r.errorf("%s.input: must be a JSON Schema whose type is \"object\"", field)
			}
		}
	}

	// config must be a JSON Schema whose type is object.
	if cfg, ok := m["config"]; ok && !isObjectSchema(cfg) {
		r.errorf("config: must be a JSON Schema whose type is \"object\"")
	}

	// icon: should exist (a missing icon falls back to the default).
	if icon, ok := m["icon"].(string); ok && !hasDotDot(icon) {
		if _, err := fs.Stat(fsys, icon); err != nil {
			r.warnf("icon: %q does not exist in the package", icon)
		}
	}

	checkLocales(fsys, m, r)
	checkEngines(m, r)
}

// manifestRef is one manifest field holding a package-relative path.
type manifestRef struct {
	field, value string
}

func manifestPaths(m map[string]any) []manifestRef {
	var out []manifestRef
	if fe, ok := m["frontend"].(map[string]any); ok {
		if s, ok := fe["entry"].(string); ok {
			out = append(out, manifestRef{"frontend.entry", s})
		}
		if styles, ok := fe["styles"].([]any); ok {
			for i, st := range styles {
				if s, ok := st.(string); ok {
					out = append(out, manifestRef{fmt.Sprintf("frontend.styles[%d]", i), s})
				}
			}
		}
	}
	if autos, ok := m["automations"].([]any); ok {
		for i, a := range autos {
			if am, ok := a.(map[string]any); ok {
				if s, ok := am["entry"].(string); ok {
					out = append(out, manifestRef{fmt.Sprintf("automations[%d].entry", i), s})
				}
			}
		}
	}
	for _, key := range []string{"icon", "locales"} {
		if s, ok := m[key].(string); ok {
			out = append(out, manifestRef{key, s})
		}
	}
	return out
}

func hasDotDot(p string) bool {
	for _, seg := range strings.Split(strings.ReplaceAll(p, `\`, "/"), "/") {
		if seg == ".." {
			return true
		}
	}
	return false
}

// checkFile reports a missing (or non-regular) package file; true when present.
func checkFile(fsys fs.FS, field, p string, r *Result) bool {
	clean := path.Clean(p)
	if !fs.ValidPath(clean) {
		return false // the schema's relativePath pattern already reports it
	}
	info, err := fs.Stat(fsys, clean)
	if err != nil {
		r.errorf("%s: %q does not exist in the package", field, p)
		return false
	}
	if !info.Mode().IsRegular() {
		r.errorf("%s: %q is not a file", field, p)
		return false
	}
	return true
}

func isObjectSchema(v any) bool {
	obj, ok := v.(map[string]any)
	return ok && obj["type"] == "object"
}

// LocalesDir returns the manifest's locales directory (default "locales").
func LocalesDir(m map[string]any) string {
	if s, ok := m["locales"].(string); ok && s != "" {
		return strings.TrimSuffix(s, "/")
	}
	return DefaultLocalesDir
}

var localeFileRe = regexp.MustCompile(`^[a-z]{2}(-[A-Z]{2})?\.json$`)

// checkLocales: each locales/<lang>.json must be a JSON object.
func checkLocales(fsys fs.FS, m map[string]any, r *Result) {
	dir := LocalesDir(m)
	if hasDotDot(dir) || !fs.ValidPath(path.Clean(dir)) {
		return
	}
	_, declared := m["locales"]
	entries, err := fs.ReadDir(fsys, path.Clean(dir))
	if err != nil {
		if declared {
			r.warnf("locales: %q does not exist in the package", dir)
		}
		return
	}
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		name := path.Join(path.Clean(dir), e.Name())
		if !localeFileRe.MatchString(e.Name()) {
			r.warnf("%s: locale files are named <lang>.json (e.g. zh-CN.json, en-US.json)", name)
		}
		data, err := fs.ReadFile(fsys, name)
		if err != nil {
			r.errorf("%s: %v", name, err)
			continue
		}
		v, err := decodeJSON(data)
		if err != nil {
			r.errorf("%s: invalid JSON: %v", name, err)
			continue
		}
		if _, ok := v.(map[string]any); !ok {
			r.errorf("%s: must be a JSON object of message keys", name)
		}
	}
}

// checkEngines warns when engines.valuz-plugin-api excludes this plugin API
// (Valuz then marks the plugin incompatible instead of loading it).
func checkEngines(m map[string]any, r *Result) {
	engines, ok := m["engines"].(map[string]any)
	if !ok {
		return
	}
	rng, ok := engines["valuz-plugin-api"].(string)
	if !ok || strings.TrimSpace(rng) == "" {
		return
	}
	in, err := RangeIncludes(rng, PluginAPIVersion)
	if err != nil {
		r.warnf("engines.valuz-plugin-api: cannot parse %q as a SemVer range: %v", rng, err)
		return
	}
	if !in {
		r.warnf("engines.valuz-plugin-api: %q does not include plugin API %s; Valuz will mark the plugin incompatible", rng, PluginAPIVersion)
	}
}

// checkSymlinkEscapes rejects manifest paths that resolve outside dir.
func checkSymlinkEscapes(dir string, r *Result) {
	realRoot, err := filepath.EvalSymlinks(dir)
	if err != nil {
		return
	}
	for _, ref := range manifestPaths(r.Manifest) {
		if hasDotDot(ref.value) {
			continue
		}
		resolved, err := filepath.EvalSymlinks(filepath.Join(dir, filepath.FromSlash(ref.value)))
		if err != nil {
			continue // missing files are reported by the existence checks
		}
		if !within(realRoot, resolved) {
			r.errorf("%s: %q resolves outside the package", ref.field, ref.value)
		}
	}
}

func within(root, p string) bool {
	rel, err := filepath.Rel(root, p)
	if err != nil {
		return false
	}
	return rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) && !filepath.IsAbs(rel)
}

// ReservedPrefixes returns the id prefixes reserved for first-party plugins,
// read from the schema's x-valuz-rules so the list cannot drift.
func ReservedPrefixes() []string {
	root, err := loadSchema()
	if err != nil {
		return nil
	}
	rules, _ := root["x-valuz-rules"].([]any)
	const lead = "id must not start with a reserved prefix:"
	for _, rule := range rules {
		s, _ := rule.(string)
		if !strings.HasPrefix(s, lead) {
			continue
		}
		var out []string
		for _, p := range strings.Split(strings.TrimPrefix(s, lead), ",") {
			if p = strings.TrimSpace(p); p != "" {
				out = append(out, p)
			}
		}
		sort.Strings(out)
		return out
	}
	return nil
}

// LocalizedText picks a display string from a localizedText value
// (string, or {lang: text}; en-US, en, zh-CN, then the first key).
func LocalizedText(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case map[string]any:
		for _, lang := range []string{"en-US", "en", "zh-CN", "zh"} {
			if s, ok := t[lang].(string); ok && s != "" {
				return s
			}
		}
		for _, k := range sortedKeys(t) {
			if s, ok := t[k].(string); ok && s != "" {
				return s
			}
		}
	}
	return ""
}

func stringField(m map[string]any, key string) string {
	if m == nil {
		return ""
	}
	s, _ := m[key].(string)
	return s
}

// StringList reads a manifest string array (non-strings skipped).
func StringList(m map[string]any, key string) []string {
	arr, _ := m[key].([]any)
	out := make([]string, 0, len(arr))
	for _, v := range arr {
		if s, ok := v.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

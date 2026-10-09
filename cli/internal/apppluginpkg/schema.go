// Package apppluginpkg validates and packs third-party Valuz plugins
// (valuz-plugin.json, manifestVersion 1) for `valuz plugin validate|pack|publish`.
//
// The manifest schema is an embedded, byte-identical copy of the SDK's
// frontend/packages/plugin-sdk/valuz-plugin.schema.json (a test pins the
// copy). The structural rules are evaluated straight from that schema by a
// small JSON Schema evaluator covering the keywords the schema uses; the
// rules a schema cannot express (its "x-valuz-rules") are coded in
// validate.go.
package apppluginpkg

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"fmt"
	"math"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"unicode/utf8"
)

//go:embed valuz-plugin.schema.json
var schemaJSON []byte

// SchemaJSON returns the embedded manifest schema.
func SchemaJSON() []byte { return append([]byte(nil), schemaJSON...) }

var (
	schemaOnce sync.Once
	schemaRoot map[string]any
	schemaErr  error
)

func loadSchema() (map[string]any, error) {
	schemaOnce.Do(func() {
		v, err := decodeJSON(schemaJSON)
		if err != nil {
			schemaErr = fmt.Errorf("embedded manifest schema: %w", err)
			return
		}
		root, ok := v.(map[string]any)
		if !ok {
			schemaErr = fmt.Errorf("embedded manifest schema is not an object")
			return
		}
		schemaRoot = root
	})
	return schemaRoot, schemaErr
}

// decodeJSON decodes one JSON document keeping numbers as json.Number so
// integer/number checks are exact.
func decodeJSON(data []byte) (any, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, err
	}
	if dec.More() {
		return nil, fmt.Errorf("unexpected data after the JSON document")
	}
	return v, nil
}

// issue is one validation finding anchored at a JSON path ("" = document).
type issue struct {
	Path    string
	Message string
}

func (i issue) String() string {
	if i.Path == "" {
		return i.Message
	}
	return i.Path + ": " + i.Message
}

func childPath(path, key string) string {
	if path == "" {
		return key
	}
	return path + "." + key
}

func indexPath(path string, i int) string {
	return fmt.Sprintf("%s[%d]", path, i)
}

// evaluator applies the subset of JSON Schema 2020-12 the manifest schema
// uses: $ref (local), type, const, enum, required, properties,
// additionalProperties, propertyNames, minProperties, items, minItems,
// maxItems, uniqueItems, minLength, maxLength, pattern, minimum, maximum,
// oneOf. Annotations (description, default, title, x-*) are ignored.
type evaluator struct {
	root    map[string]any
	mu      sync.Mutex
	regexps map[string]*regexp.Regexp
}

func newEvaluator(root map[string]any) *evaluator {
	return &evaluator{root: root, regexps: map[string]*regexp.Regexp{}}
}

func (e *evaluator) regexp(pattern string) (*regexp.Regexp, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if re, ok := e.regexps[pattern]; ok {
		return re, nil
	}
	re, err := regexp.Compile(pattern)
	if err != nil {
		return nil, err
	}
	e.regexps[pattern] = re
	return re, nil
}

func (e *evaluator) resolve(ref string) (map[string]any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("unsupported $ref %q", ref)
	}
	var cur any = e.root
	for _, part := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		obj, ok := cur.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("unresolvable $ref %q", ref)
		}
		cur = obj[part]
	}
	target, ok := cur.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("unresolvable $ref %q", ref)
	}
	return target, nil
}

func (e *evaluator) eval(s map[string]any, v any, path string) []issue {
	var out []issue
	if ref, ok := s["$ref"].(string); ok {
		target, err := e.resolve(ref)
		if err != nil {
			return []issue{{path, err.Error()}}
		}
		out = append(out, e.eval(target, v, path)...)
	}
	if t, ok := s["type"].(string); ok && !typeMatches(t, v) {
		return append(out, issue{path, "must be " + article(t)})
	}
	if c, ok := s["const"]; ok && !jsonEqual(c, v) {
		out = append(out, issue{path, "must be " + compactJSON(c)})
	}
	if enum, ok := s["enum"].([]any); ok && !inEnum(enum, v) {
		vals := make([]string, 0, len(enum))
		for _, x := range enum {
			vals = append(vals, compactJSON(x))
		}
		out = append(out, issue{path, fmt.Sprintf("%s is not one of: %s", compactJSON(v), strings.Join(vals, ", "))})
	}
	if branches, ok := s["oneOf"].([]any); ok {
		out = append(out, e.evalOneOf(branches, v, path)...)
	}
	switch val := v.(type) {
	case map[string]any:
		out = append(out, e.evalObject(s, val, path)...)
	case []any:
		out = append(out, e.evalArray(s, val, path)...)
	case string:
		out = append(out, e.evalString(s, val, path)...)
	case json.Number:
		out = append(out, evalNumber(s, val, path)...)
	}
	return out
}

func (e *evaluator) evalObject(s map[string]any, obj map[string]any, path string) []issue {
	var out []issue
	if req, ok := s["required"].([]any); ok {
		for _, r := range req {
			name, _ := r.(string)
			if _, present := obj[name]; !present {
				out = append(out, issue{childPath(path, name), "is required"})
			}
		}
	}
	props, _ := s["properties"].(map[string]any)
	for _, key := range sortedKeys(obj) {
		if ps, ok := props[key].(map[string]any); ok {
			out = append(out, e.eval(ps, obj[key], childPath(path, key))...)
			continue
		}
		switch ap := s["additionalProperties"].(type) {
		case bool:
			if !ap {
				out = append(out, issue{childPath(path, key), "is not a known field"})
			}
		case map[string]any:
			out = append(out, e.eval(ap, obj[key], childPath(path, key))...)
		}
	}
	if pn, ok := s["propertyNames"].(map[string]any); ok {
		for _, key := range sortedKeys(obj) {
			for _, is := range e.eval(pn, key, "") {
				out = append(out, issue{path, fmt.Sprintf("key %q: %s", key, is.Message)})
			}
		}
	}
	if n, ok := intKeyword(s, "minProperties"); ok && len(obj) < n {
		out = append(out, issue{path, fmt.Sprintf("must have at least %d entr%s", n, plural(n, "y", "ies"))})
	}
	return out
}

func (e *evaluator) evalArray(s map[string]any, arr []any, path string) []issue {
	var out []issue
	if n, ok := intKeyword(s, "minItems"); ok && len(arr) < n {
		out = append(out, issue{path, fmt.Sprintf("must have at least %d item%s", n, plural(n, "", "s"))})
	}
	if n, ok := intKeyword(s, "maxItems"); ok && len(arr) > n {
		out = append(out, issue{path, fmt.Sprintf("must have at most %d item%s", n, plural(n, "", "s"))})
	}
	if u, _ := s["uniqueItems"].(bool); u {
		seen := map[string]bool{}
		for _, item := range arr {
			key := compactJSON(item)
			if seen[key] {
				out = append(out, issue{path, fmt.Sprintf("must not contain duplicates (%s)", key)})
				continue
			}
			seen[key] = true
		}
	}
	if items, ok := s["items"].(map[string]any); ok {
		for i, item := range arr {
			out = append(out, e.eval(items, item, indexPath(path, i))...)
		}
	}
	return out
}

func (e *evaluator) evalString(s map[string]any, str, path string) []issue {
	var out []issue
	n := utf8.RuneCountInString(str)
	if min, ok := intKeyword(s, "minLength"); ok && n < min {
		if min == 1 {
			out = append(out, issue{path, "must not be empty"})
		} else {
			out = append(out, issue{path, fmt.Sprintf("must be at least %d characters", min)})
		}
	}
	if max, ok := intKeyword(s, "maxLength"); ok && n > max {
		out = append(out, issue{path, fmt.Sprintf("must be at most %d characters", max)})
	}
	if pattern, ok := s["pattern"].(string); ok {
		re, err := e.regexp(pattern)
		if err != nil {
			out = append(out, issue{path, fmt.Sprintf("schema pattern %q does not compile: %v", pattern, err)})
		} else if !re.MatchString(str) {
			msg := fmt.Sprintf("%q does not match %s", str, pattern)
			if desc, ok := s["description"].(string); ok && desc != "" {
				msg += " (" + desc + ")"
			}
			out = append(out, issue{path, msg})
		}
	}
	return out
}

func evalNumber(s map[string]any, num json.Number, path string) []issue {
	var out []issue
	f, err := num.Float64()
	if err != nil {
		return []issue{{path, fmt.Sprintf("invalid number %s", num)}}
	}
	if min, ok := numberKeyword(s, "minimum"); ok && f < min {
		out = append(out, issue{path, fmt.Sprintf("must be at least %s", formatFloat(min))})
	}
	if max, ok := numberKeyword(s, "maximum"); ok && f > max {
		out = append(out, issue{path, fmt.Sprintf("must be at most %s", formatFloat(max))})
	}
	return out
}

// evalOneOf passes when exactly one branch passes. On failure it reports the
// findings of the branch closest to the value (same declared type, fewest
// findings) so the message names what to fix rather than "no branch matched".
func (e *evaluator) evalOneOf(branches []any, v any, path string) []issue {
	type result struct {
		schema map[string]any
		issues []issue
	}
	results := make([]result, 0, len(branches))
	passed := 0
	for _, b := range branches {
		bs, _ := b.(map[string]any)
		is := e.eval(bs, v, path)
		if len(is) == 0 {
			passed++
		}
		results = append(results, result{bs, is})
	}
	if passed == 1 {
		return nil
	}
	if passed > 1 {
		return []issue{{path, "matches more than one allowed form"}}
	}
	var best *result
	// Prefer a branch declaring the value's own type, then untyped branches.
	for _, wantTyped := range []bool{true, false} {
		for i := range results {
			r := &results[i]
			t, typed := r.schema["type"].(string)
			if wantTyped && (!typed || !typeMatches(t, v)) {
				continue
			}
			if !wantTyped && typed {
				continue
			}
			if best == nil || len(r.issues) < len(best.issues) {
				best = r
			}
		}
		if best != nil {
			return best.issues
		}
	}
	forms := []string{}
	seen := map[string]bool{}
	for _, r := range results {
		form := "an allowed value"
		if t, ok := r.schema["type"].(string); ok {
			form = article(t)
		} else if c, ok := r.schema["const"]; ok {
			form = compactJSON(c)
		}
		if !seen[form] {
			seen[form] = true
			forms = append(forms, form)
		}
	}
	return []issue{{path, "must be " + joinOr(forms)}}
}

func typeMatches(t string, v any) bool {
	switch t {
	case "object":
		_, ok := v.(map[string]any)
		return ok
	case "array":
		_, ok := v.([]any)
		return ok
	case "string":
		_, ok := v.(string)
		return ok
	case "boolean":
		_, ok := v.(bool)
		return ok
	case "null":
		return v == nil
	case "number":
		_, ok := v.(json.Number)
		return ok
	case "integer":
		n, ok := v.(json.Number)
		if !ok {
			return false
		}
		if _, err := n.Int64(); err == nil {
			return true
		}
		f, err := n.Float64()
		return err == nil && f == math.Trunc(f) && !math.IsInf(f, 0)
	}
	return false
}

func article(t string) string {
	switch t {
	case "object", "array", "integer":
		return "an " + t
	}
	return "a " + t
}

func joinOr(items []string) string {
	switch len(items) {
	case 0:
		return ""
	case 1:
		return items[0]
	}
	return strings.Join(items[:len(items)-1], ", ") + " or " + items[len(items)-1]
}

func plural(n int, one, many string) string {
	if n == 1 {
		return one
	}
	return many
}

// normalize turns json.Number into float64 so equal values compare equal
// regardless of spelling (1 vs 1.0).
func normalize(v any) any {
	switch x := v.(type) {
	case json.Number:
		f, err := x.Float64()
		if err != nil {
			return x.String()
		}
		return f
	case map[string]any:
		out := make(map[string]any, len(x))
		for k, val := range x {
			out[k] = normalize(val)
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, val := range x {
			out[i] = normalize(val)
		}
		return out
	}
	return v
}

func jsonEqual(a, b any) bool {
	return reflect.DeepEqual(normalize(a), normalize(b))
}

func inEnum(enum []any, v any) bool {
	for _, x := range enum {
		if jsonEqual(x, v) {
			return true
		}
	}
	return false
}

func compactJSON(v any) string {
	raw, err := json.Marshal(v)
	if err != nil {
		return fmt.Sprint(v)
	}
	return string(raw)
}

func sortedKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func intKeyword(s map[string]any, key string) (int, bool) {
	f, ok := numberKeyword(s, key)
	if !ok {
		return 0, false
	}
	return int(f), true
}

func numberKeyword(s map[string]any, key string) (float64, bool) {
	n, ok := s[key].(json.Number)
	if !ok {
		return 0, false
	}
	f, err := n.Float64()
	return f, err == nil
}

func formatFloat(f float64) string {
	return strconv.FormatFloat(f, 'f', -1, 64)
}

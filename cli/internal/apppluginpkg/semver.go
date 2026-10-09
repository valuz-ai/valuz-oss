package apppluginpkg

import (
	"fmt"
	"strconv"
	"strings"
)

// PluginAPIVersion is the plugin API this CLI validates against
// (engines.valuz-plugin-api must include it).
const PluginAPIVersion = "1.0.0"

// semver is a parsed SemVer 2.0 version (build metadata dropped).
type semver struct {
	major, minor, patch int
	pre                 []string
}

func parseSemver(s string) (semver, error) {
	s = strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(s), "v"))
	if i := strings.IndexByte(s, '+'); i >= 0 {
		s = s[:i]
	}
	var v semver
	core := s
	if i := strings.IndexByte(s, '-'); i >= 0 {
		core = s[:i]
		v.pre = strings.Split(s[i+1:], ".")
	}
	parts := strings.Split(core, ".")
	if len(parts) != 3 {
		return v, fmt.Errorf("%q is not a SemVer version", s)
	}
	nums := [3]int{}
	for i, p := range parts {
		n, err := strconv.Atoi(p)
		if err != nil || n < 0 {
			return v, fmt.Errorf("%q is not a SemVer version", s)
		}
		nums[i] = n
	}
	v.major, v.minor, v.patch = nums[0], nums[1], nums[2]
	return v, nil
}

// compare orders versions by SemVer precedence (-1, 0, 1).
func (a semver) compare(b semver) int {
	for _, d := range [3]int{a.major - b.major, a.minor - b.minor, a.patch - b.patch} {
		if d < 0 {
			return -1
		}
		if d > 0 {
			return 1
		}
	}
	switch {
	case len(a.pre) == 0 && len(b.pre) == 0:
		return 0
	case len(a.pre) == 0:
		return 1
	case len(b.pre) == 0:
		return -1
	}
	for i := 0; i < len(a.pre) && i < len(b.pre); i++ {
		if c := comparePreID(a.pre[i], b.pre[i]); c != 0 {
			return c
		}
	}
	switch {
	case len(a.pre) < len(b.pre):
		return -1
	case len(a.pre) > len(b.pre):
		return 1
	}
	return 0
}

func comparePreID(a, b string) int {
	an, aerr := strconv.Atoi(a)
	bn, berr := strconv.Atoi(b)
	switch {
	case aerr == nil && berr == nil:
		switch {
		case an < bn:
			return -1
		case an > bn:
			return 1
		}
		return 0
	case aerr == nil:
		return -1
	case berr == nil:
		return 1
	}
	return strings.Compare(a, b)
}

// CompareVersions orders two SemVer strings; unparseable versions sort first.
func CompareVersions(a, b string) int {
	va, aerr := parseSemver(a)
	vb, berr := parseSemver(b)
	switch {
	case aerr != nil && berr != nil:
		return strings.Compare(a, b)
	case aerr != nil:
		return -1
	case berr != nil:
		return 1
	}
	return va.compare(vb)
}

// comparator is one bound: op is one of "", ">", ">=", "<", "<=".
type comparator struct {
	op string
	v  semver
}

func (c comparator) test(v semver) bool {
	d := v.compare(c.v)
	switch c.op {
	case ">":
		return d > 0
	case ">=":
		return d >= 0
	case "<":
		return d < 0
	case "<=":
		return d <= 0
	}
	return d == 0
}

// RangeIncludes reports whether the npm-style SemVer range includes version
// (supports ||, space-joined comparators, hyphen ranges, x/* wildcards, ^ and ~).
func RangeIncludes(rng, version string) (bool, error) {
	v, err := parseSemver(version)
	if err != nil {
		return false, err
	}
	for _, set := range strings.Split(rng, "||") {
		comps, err := parseComparatorSet(set)
		if err != nil {
			return false, err
		}
		ok := true
		for _, c := range comps {
			if !c.test(v) {
				ok = false
				break
			}
		}
		if ok {
			return true, nil
		}
	}
	return false, nil
}

func parseComparatorSet(set string) ([]comparator, error) {
	fields := strings.Fields(set)
	// Hyphen range: "1.2.3 - 2.3.4".
	if len(fields) == 3 && fields[1] == "-" {
		lo, err := parsePartial(fields[0])
		if err != nil {
			return nil, err
		}
		hi, err := parsePartial(fields[2])
		if err != nil {
			return nil, err
		}
		out := []comparator{}
		if !lo.any() {
			out = append(out, comparator{">=", lo.floor()})
		}
		if !hi.any() {
			if hi.n == 3 {
				out = append(out, comparator{"<=", hi.floor()})
			} else {
				out = append(out, comparator{"<", hi.bump()})
			}
		}
		return out, nil
	}
	// Join an operator separated from its version: ">= 1.2.3".
	var tokens []string
	for i := 0; i < len(fields); i++ {
		f := fields[i]
		if isOperator(f) && i+1 < len(fields) {
			f += fields[i+1]
			i++
		}
		tokens = append(tokens, f)
	}
	var out []comparator
	for _, tok := range tokens {
		cs, err := parseComparator(tok)
		if err != nil {
			return nil, err
		}
		out = append(out, cs...)
	}
	return out, nil
}

func isOperator(s string) bool {
	switch s {
	case "<", "<=", ">", ">=", "=", "^", "~", "~>":
		return true
	}
	return false
}

// partial is a possibly-wildcarded version: n = number of concrete parts.
type partial struct {
	parts [3]int
	n     int
	pre   []string
}

func (p partial) any() bool { return p.n == 0 }

func (p partial) floor() semver {
	return semver{p.parts[0], p.parts[1], p.parts[2], p.pre}
}

// bump returns the exclusive upper bound of the wildcard part.
func (p partial) bump() semver {
	switch p.n {
	case 1:
		return semver{major: p.parts[0] + 1}
	case 2:
		return semver{major: p.parts[0], minor: p.parts[1] + 1}
	}
	return semver{p.parts[0], p.parts[1], p.parts[2] + 1, nil}
}

func parsePartial(s string) (partial, error) {
	var p partial
	s = strings.TrimPrefix(strings.TrimSpace(s), "v")
	if i := strings.IndexByte(s, '+'); i >= 0 {
		s = s[:i]
	}
	if i := strings.IndexByte(s, '-'); i >= 0 {
		p.pre = strings.Split(s[i+1:], ".")
		s = s[:i]
	}
	if s == "" || s == "*" || s == "x" || s == "X" {
		return p, nil
	}
	parts := strings.Split(s, ".")
	if len(parts) > 3 {
		return p, fmt.Errorf("invalid version %q in range", s)
	}
	for i, part := range parts {
		if part == "*" || part == "x" || part == "X" {
			break
		}
		n, err := strconv.Atoi(part)
		if err != nil || n < 0 {
			return p, fmt.Errorf("invalid version %q in range", s)
		}
		p.parts[i] = n
		p.n = i + 1
	}
	return p, nil
}

func parseComparator(tok string) ([]comparator, error) {
	op := ""
	for _, candidate := range []string{">=", "<=", "~>", ">", "<", "=", "^", "~"} {
		if strings.HasPrefix(tok, candidate) {
			op = candidate
			tok = tok[len(candidate):]
			break
		}
	}
	p, err := parsePartial(tok)
	if err != nil {
		return nil, err
	}
	switch op {
	case "^":
		if p.any() {
			return nil, nil
		}
		var hi semver
		switch {
		case p.parts[0] > 0 || p.n == 1:
			hi = semver{major: p.parts[0] + 1}
		case p.parts[1] > 0 || p.n == 2:
			hi = semver{minor: p.parts[1] + 1}
		default:
			hi = semver{patch: p.parts[2] + 1}
		}
		return []comparator{{">=", p.floor()}, {"<", hi}}, nil
	case "~", "~>":
		if p.any() {
			return nil, nil
		}
		hi := semver{major: p.parts[0] + 1}
		if p.n >= 2 {
			hi = semver{major: p.parts[0], minor: p.parts[1] + 1}
		}
		return []comparator{{">=", p.floor()}, {"<", hi}}, nil
	case ">":
		if p.any() {
			return []comparator{{"<", semver{}}}, nil // nothing is greater than *
		}
		if p.n < 3 {
			return []comparator{{">=", p.bump()}}, nil
		}
		return []comparator{{">", p.floor()}}, nil
	case ">=":
		if p.any() {
			return nil, nil
		}
		return []comparator{{">=", p.floor()}}, nil
	case "<":
		if p.any() {
			return []comparator{{"<", semver{}}}, nil
		}
		return []comparator{{"<", p.floor()}}, nil
	case "<=":
		if p.any() {
			return nil, nil
		}
		if p.n < 3 {
			return []comparator{{"<", p.bump()}}, nil
		}
		return []comparator{{"<=", p.floor()}}, nil
	}
	// "" or "=": exact for a full version, a wildcard range otherwise.
	if p.any() {
		return nil, nil
	}
	if p.n == 3 {
		return []comparator{{"", p.floor()}}, nil
	}
	return []comparator{{">=", p.floor()}, {"<", p.bump()}}, nil
}

package apppluginpkg

import "testing"

func TestRangeIncludesPluginAPI(t *testing.T) {
	cases := []struct {
		rng  string
		want bool
	}{
		{"^1.0.0", true},
		{"^1", true},
		{"^1.x", true},
		{"~1.0.0", true},
		{"~1.0", true},
		{"1.x", true},
		{"1", true},
		{"*", true},
		{"x", true},
		{">=1.0.0", true},
		{">= 1.0.0 < 2", true},
		{">=0.9.0 <1.0.0", false},
		{"1.0.0", true},
		{"=1.0.0", true},
		{"1.0.1", false},
		{"^1.1.0", false},
		{"^0.9.0", false},
		{"^2.0.0", false},
		{"~0.1", false},
		{"0.9.0 - 1.0.0", true},
		{"0.9.0 - 1", true},
		{"1.1 - 2", false},
		{"^2.0.0 || ^1.0.0", true},
		{">1.0.0", false},
		{">1", false},
		{">0", true},
		{"<=1.0.0", true},
		{"<1.0.0", false},
		{"<1.0.0-0", false},
		{">=1.0.0-beta", true},
	}
	for _, c := range cases {
		got, err := RangeIncludes(c.rng, PluginAPIVersion)
		if err != nil {
			t.Errorf("%q: %v", c.rng, err)
			continue
		}
		if got != c.want {
			t.Errorf("RangeIncludes(%q, %s) = %v, want %v", c.rng, PluginAPIVersion, got, c.want)
		}
	}
}

func TestRangeIncludesRejectsGarbage(t *testing.T) {
	for _, rng := range []string{"latest", "^a.b", "1.2.3.4"} {
		if _, err := RangeIncludes(rng, PluginAPIVersion); err == nil {
			t.Errorf("%q: want a parse error", rng)
		}
	}
}

func TestCompareVersions(t *testing.T) {
	cases := []struct {
		a, b string
		want int
	}{
		{"1.0.0", "1.0.0", 0},
		{"1.0.0", "1.0.1", -1},
		{"1.10.0", "1.9.0", 1},
		{"1.0.0-alpha", "1.0.0", -1},
		{"1.0.0-alpha.2", "1.0.0-alpha.10", -1},
		{"1.0.0-alpha", "1.0.0-alpha.1", -1},
		{"1.0.0-beta", "1.0.0-alpha.9", 1},
		{"1.0.0+build", "1.0.0", 0},
	}
	for _, c := range cases {
		if got := CompareVersions(c.a, c.b); got != c.want {
			t.Errorf("CompareVersions(%q, %q) = %d, want %d", c.a, c.b, got, c.want)
		}
	}
}

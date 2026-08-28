package version

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestCurrentJSON(t *testing.T) {
	info := Current(true)
	data, err := info.JSON()
	if err != nil {
		t.Fatalf("JSON: %v", err)
	}

	var decoded map[string]any
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}

	for _, field := range []string{"version", "commit", "build_time", "go", "os", "arch", "output_schemas", "capabilities"} {
		if _, ok := decoded[field]; !ok {
			t.Fatalf("missing field %q in %s", field, data)
		}
	}

	schemas, _ := decoded["output_schemas"].([]any)
	if len(schemas) != 2 {
		t.Fatalf("expected 2 output schemas, got %d", len(schemas))
	}
	if !strings.Contains(string(data), RunResultSchema) || !strings.Contains(string(data), RunEventSchema) {
		t.Fatalf("output_schemas missing contracts: %s", data)
	}
}

func TestString(t *testing.T) {
	if got := (Info{Version: "1.2.3", Commit: "abc"}).String(); got != "1.2.3 (abc)" {
		t.Fatalf("String = %q", got)
	}
	if got := (Info{Version: "1.2.3", Commit: "unknown"}).String(); got != "1.2.3" {
		t.Fatalf("String = %q", got)
	}
}
package apppluginpkg

import (
	"bytes"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

// The embedded schema must stay byte-identical to the SDK's canonical copy;
// update internal/apppluginpkg/valuz-plugin.schema.json whenever it changes.
func TestEmbeddedSchemaMatchesSDKSchema(t *testing.T) {
	sdk := filepath.Join("..", "..", "..", "frontend", "packages", "plugin-sdk", "valuz-plugin.schema.json")
	want, err := os.ReadFile(sdk)
	if err != nil {
		t.Fatalf("read the SDK schema at %s: %v", sdk, err)
	}
	if !bytes.Equal(SchemaJSON(), want) {
		t.Fatalf("internal/apppluginpkg/valuz-plugin.schema.json differs from %s; copy it over", sdk)
	}
}

func TestEmbeddedSchemaLoads(t *testing.T) {
	root, err := loadSchema()
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := root["x-valuz-rules"].([]any); !ok {
		t.Fatal("schema has no x-valuz-rules")
	}
}

func TestReservedPrefixesComeFromTheSchemaRules(t *testing.T) {
	got := ReservedPrefixes()
	want := []string{"commercial-", "commercial.", "edition.", "finance-", "finance.", "oss-", "team-", "team.", "valuz."}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("reserved prefixes = %v, want %v", got, want)
	}
}

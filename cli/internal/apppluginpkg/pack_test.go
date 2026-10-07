package apppluginpkg

import (
	"archive/zip"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"
)

// packFixture is baseManifest/baseFiles plus files that must NOT be packed.
func packFixture(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	files := baseFiles()
	for name, content := range map[string]string{
		"frontend/chunks/a.js":         "export const a = 1",
		"LICENSE.third-party.md":       "notices",
		"frontend/src/index.tsx":       "source",
		"frontend/node_modules/x/a.js": "dep",
		"frontend/.env":                "SECRET=1",
		"frontend/.DS_Store":           "junk",
		"src/index.tsx":                "source",
		"node_modules/react/index.js":  "dep",
		".git/HEAD":                    "ref",
		"package.json":                 "{}",
		"docs/notes.md":                "notes",
		"automations/.cache/x":         "cache",
		"readme.txt":                   "not README.md",
	} {
		files[name] = content
	}
	writePlugin(t, dir, baseManifest(), files)
	return dir
}

func TestPackFileSet(t *testing.T) {
	dir := packFixture(t)
	res, err := Pack(dir, "")
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		"LICENSE",
		"LICENSE.third-party.md",
		"README.md",
		"automations/daily.py",
		"automations/run.sh",
		"frontend/chunks/a.js",
		"frontend/index.css",
		"frontend/index.js",
		"icon.png",
		"locales/en-US.json",
		"locales/zh-CN.json",
		"valuz-plugin.json",
	}
	if !reflect.DeepEqual(res.Files, want) {
		t.Fatalf("files =\n%q\nwant\n%q", res.Files, want)
	}
	if res.Path != filepath.Join(dir, "dist", "acme.hello-1.2.0.zip") {
		t.Fatalf("path = %s", res.Path)
	}
	if res.ID != "acme.hello" || res.Version != "1.2.0" || len(res.SHA256) != 64 {
		t.Fatalf("unexpected result %+v", res)
	}
	info, err := os.Stat(res.Path)
	if err != nil || info.Size() != res.Size {
		t.Fatalf("zip on disk: %v (size %d vs %d)", err, info.Size(), res.Size)
	}

	zr, err := zip.OpenReader(res.Path)
	if err != nil {
		t.Fatal(err)
	}
	defer zr.Close()
	names := []string{}
	for _, f := range zr.File {
		names = append(names, f.Name)
		if !f.Modified.Equal(packMTime) {
			t.Errorf("%s: mtime %v, want the fixed %v", f.Name, f.Modified, packMTime)
		}
		if f.Mode().Perm() != 0o644 {
			t.Errorf("%s: mode %v", f.Name, f.Mode())
		}
	}
	if !sort.StringsAreSorted(names) || !reflect.DeepEqual(names, want) {
		t.Fatalf("zip entries %q", names)
	}
}

func TestPackIsDeterministic(t *testing.T) {
	dir := packFixture(t)
	first, err := Pack(dir, filepath.Join(t.TempDir(), "a"))
	if err != nil {
		t.Fatal(err)
	}
	// Touch every source file: mtimes must not leak into the zip.
	later := time.Now().Add(time.Hour)
	_ = filepath.WalkDir(dir, func(p string, d os.DirEntry, err error) error {
		if err == nil && !d.IsDir() {
			_ = os.Chtimes(p, later, later)
		}
		return nil
	})
	second, err := Pack(dir, filepath.Join(t.TempDir(), "b"))
	if err != nil {
		t.Fatal(err)
	}
	if first.SHA256 != second.SHA256 || first.Size != second.Size {
		t.Fatalf("sha256 differs between packs: %s vs %s", first.SHA256, second.SHA256)
	}
	a, _ := os.ReadFile(first.Path)
	if SHA256Hex(a) != first.SHA256 {
		t.Fatal("reported sha256 is not the file's")
	}
}

func TestPackDefaultOutputIsNotRepacked(t *testing.T) {
	dir := packFixture(t)
	first, err := Pack(dir, "")
	if err != nil {
		t.Fatal(err)
	}
	second, err := Pack(dir, "")
	if err != nil {
		t.Fatal(err)
	}
	if first.SHA256 != second.SHA256 {
		t.Fatal("the previous zip in dist/ leaked into the next pack")
	}
}

func TestPackCustomLocalesDir(t *testing.T) {
	dir := t.TempDir()
	m := baseManifest()
	m["locales"] = "i18n/messages"
	files := baseFiles()
	delete(files, "locales/en-US.json")
	delete(files, "locales/zh-CN.json")
	files["i18n/messages/en-US.json"] = `{"title":"Hello"}`
	writePlugin(t, dir, m, files)
	res, err := Pack(dir, "")
	if err != nil {
		t.Fatal(err)
	}
	if !containsSub(res.Files, "i18n/messages/en-US.json") {
		t.Fatalf("custom locales dir not packed: %q", res.Files)
	}
}

func TestPackRefusesInvalidPlugin(t *testing.T) {
	dir := t.TempDir()
	m := baseManifest()
	m["id"] = "valuz.hello"
	writePlugin(t, dir, m, baseFiles())
	_, err := Pack(dir, "")
	var invalid *InvalidError
	if !errors.As(err, &invalid) || !containsSub(invalid.Result.Errors, "reserved prefix") {
		t.Fatalf("want an InvalidError, got %v", err)
	}
	if _, statErr := os.Stat(filepath.Join(dir, "dist")); !os.IsNotExist(statErr) {
		t.Fatal("dist/ written for an invalid plugin")
	}
}

func TestPackRefusesEntryOutsideThePackedSet(t *testing.T) {
	dir := t.TempDir()
	m := baseManifest()
	m["frontend"] = map[string]any{"entry": "build/index.js"}
	files := baseFiles()
	files["build/index.js"] = "export default 1"
	writePlugin(t, dir, m, files)
	res, err := ValidateDir(dir)
	if err != nil || !res.OK || !containsSub(res.Warnings, `frontend.entry: "build/index.js" is outside the packed file set`) {
		t.Fatalf("validate should pass with a warning: %v %+v", err, res)
	}
	_, err = Pack(dir, "")
	var invalid *InvalidError
	if !errors.As(err, &invalid) || !strings.Contains(invalid.Stage, "packed file set is incomplete") ||
		!containsSub(invalid.Result.Errors, `frontend.entry: "build/index.js" does not exist in the package`) {
		t.Fatalf("want the packed-set error, got %v", err)
	}
}

func TestPackRejectsSymlinkOutsideThePlugin(t *testing.T) {
	dir, outside := packFixture(t), t.TempDir()
	writeFile(t, filepath.Join(outside, "secret.js"), "export default 1")
	if err := os.Symlink(filepath.Join(outside, "secret.js"), filepath.Join(dir, "frontend", "linked.js")); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	if _, err := Pack(dir, ""); err == nil || !strings.Contains(err.Error(), "outside the plugin directory") {
		t.Fatalf("want a symlink escape error, got %v", err)
	}
}

func TestValidateZipOfAPack(t *testing.T) {
	dir := packFixture(t)
	res, err := Pack(dir, "")
	if err != nil {
		t.Fatal(err)
	}
	zres, err := ValidateZip(res.Path)
	if err != nil {
		t.Fatal(err)
	}
	if !zres.OK || zres.ID() != "acme.hello" {
		t.Fatalf("packed zip invalid: %q", zres.Errors)
	}
	data, _ := os.ReadFile(res.Path)
	m, err := ReadZipManifest(data)
	if err != nil || m["id"] != "acme.hello" {
		t.Fatalf("ReadZipManifest: %v %v", m, err)
	}
}

func TestValidateZipWithoutManifest(t *testing.T) {
	path := filepath.Join(t.TempDir(), "x.zip")
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	zw := zip.NewWriter(f)
	w, _ := zw.Create("frontend/index.js")
	_, _ = w.Write([]byte("export default 1"))
	_ = zw.Close()
	_ = f.Close()
	res, err := ValidateZip(path)
	if err != nil {
		t.Fatal(err)
	}
	if res.OK || !containsSub(res.Errors, "valuz-plugin.json not found") {
		t.Fatalf("errors = %q", res.Errors)
	}
}

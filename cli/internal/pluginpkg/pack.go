package pluginpkg

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// packMTime is the fixed modification time of every zip entry, so the same
// sources always produce the same bytes (and sha256).
var packMTime = time.Date(1980, 1, 1, 0, 0, 0, 0, time.UTC)

// skipDirs are never packed, at any depth.
var skipDirs = map[string]bool{"node_modules": true, ".git": true, "src": true}

// PackResult describes a written package.
type PackResult struct {
	Path     string   `json:"path"`
	SHA256   string   `json:"sha256"`
	Size     int64    `json:"size"`
	ID       string   `json:"id"`
	Version  string   `json:"version"`
	Files    []string `json:"files"`
	Warnings []string `json:"warnings"`
}

// InvalidError is returned when the sources (or the packed file set) fail
// validation; Result carries the findings.
type InvalidError struct {
	Result *Result
	Stage  string
}

func (e *InvalidError) Error() string {
	return fmt.Sprintf("%s: %d error(s): %s", e.Stage, len(e.Result.Errors), strings.Join(e.Result.Errors, "; "))
}

// Pack validates dir, zips the package file set deterministically into
// <outDir>/<id>-<version>.zip (outDir defaults to <dir>/dist) and returns
// its path and sha256.
//
// File set: valuz-plugin.json, frontend/, the manifest's locales directory
// (default locales/), automations/, the icon file, README.md and LICENSE*;
// node_modules, .git and src directories and dot-files are skipped.
func Pack(dir, outDir string) (*PackResult, error) {
	abs, err := filepath.Abs(dir)
	if err != nil {
		return nil, err
	}
	res, err := ValidateDir(abs)
	if err != nil {
		return nil, err
	}
	if !res.OK {
		return nil, &InvalidError{Result: res, Stage: "invalid plugin"}
	}
	if outDir == "" {
		outDir = filepath.Join(abs, "dist")
	}
	outDir, err = filepath.Abs(outDir)
	if err != nil {
		return nil, err
	}
	outPath := filepath.Join(outDir, res.ID()+"-"+res.Version()+".zip")

	files, err := FileSet(abs, res.Manifest, outPath)
	if err != nil {
		return nil, err
	}
	data, err := buildZip(abs, files)
	if err != nil {
		return nil, err
	}
	// Validate what is actually shipped: a manifest path outside the file
	// set (e.g. frontend.entry under dist/) would install broken.
	packed, err := ValidateZipBytes(data)
	if err != nil {
		return nil, err
	}
	if !packed.OK {
		return nil, &InvalidError{Result: packed, Stage: "the packed file set is incomplete (only frontend/, locales, automations/, the icon, README.md and LICENSE* are packed)"}
	}

	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return nil, err
	}
	tmp, err := os.CreateTemp(outDir, ".pack-*.zip")
	if err != nil {
		return nil, err
	}
	tmpName := tmp.Name()
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		_ = os.Remove(tmpName)
		return nil, err
	}
	if err := tmp.Close(); err != nil {
		_ = os.Remove(tmpName)
		return nil, err
	}
	if err := os.Chmod(tmpName, 0o644); err != nil {
		_ = os.Remove(tmpName)
		return nil, err
	}
	if err := os.Rename(tmpName, outPath); err != nil {
		_ = os.Remove(tmpName)
		return nil, err
	}
	sum := sha256.Sum256(data)
	return &PackResult{
		Path:     outPath,
		SHA256:   hex.EncodeToString(sum[:]),
		Size:     int64(len(data)),
		ID:       res.ID(),
		Version:  res.Version(),
		Files:    files,
		Warnings: res.Warnings,
	}, nil
}

// FileSet lists the package files of dir (slash paths, sorted). exclude is
// an absolute path never packed (the output zip itself).
func FileSet(dir string, manifest map[string]any, exclude string) ([]string, error) {
	set := map[string]bool{}
	add := func(rel string) { set[filepath.ToSlash(rel)] = true }

	if _, err := regularFile(dir, ManifestFile); err != nil {
		return nil, err
	}
	add(ManifestFile)

	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	for _, e := range entries {
		name := e.Name()
		if name != "README.md" && !strings.HasPrefix(name, "LICENSE") {
			continue
		}
		if ok, err := regularFile(dir, name); err != nil {
			return nil, err
		} else if ok {
			add(name)
		}
	}

	if icon, ok := manifest["icon"].(string); ok && icon != "" && !hasDotDot(icon) {
		if ok, err := regularFile(dir, icon); err != nil {
			return nil, err
		} else if ok && !skipped(icon) {
			add(path.Clean(icon))
		}
	}

	dirs := []string{"frontend", LocalesDir(manifest), "automations"}
	for _, sub := range dirs {
		if hasDotDot(sub) || skipped(sub) {
			continue
		}
		if err := walkPackDir(dir, path.Clean(sub), exclude, add); err != nil {
			return nil, err
		}
	}

	out := make([]string, 0, len(set))
	for f := range set {
		out = append(out, f)
	}
	sort.Strings(out)
	return out, nil
}

// skipped reports whether a slash path crosses a skipped or hidden segment.
func skipped(rel string) bool {
	for _, seg := range strings.Split(path.Clean(filepath.ToSlash(rel)), "/") {
		if skipDirs[seg] || strings.HasPrefix(seg, ".") && seg != "." {
			return true
		}
	}
	return false
}

// regularFile reports whether rel is a regular file inside root (symlinks
// are followed but must stay inside root).
func regularFile(root, rel string) (bool, error) {
	full := filepath.Join(root, filepath.FromSlash(rel))
	info, err := os.Stat(full)
	if errors.Is(err, fs.ErrNotExist) {
		if rel == ManifestFile {
			return false, fmt.Errorf("%s not found in %s", ManifestFile, root)
		}
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if !info.Mode().IsRegular() {
		return false, nil
	}
	if err := checkInside(root, full); err != nil {
		return false, err
	}
	return true, nil
}

func checkInside(root, full string) error {
	realRoot, err := filepath.EvalSymlinks(root)
	if err != nil {
		return err
	}
	real, err := filepath.EvalSymlinks(full)
	if err != nil {
		return err
	}
	if !within(realRoot, real) {
		rel, _ := filepath.Rel(root, full)
		return fmt.Errorf("%s is a symlink pointing outside the plugin directory", filepath.ToSlash(rel))
	}
	return nil
}

func walkPackDir(root, sub, exclude string, add func(string)) error {
	start := filepath.Join(root, filepath.FromSlash(sub))
	info, err := os.Stat(start)
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	if !info.IsDir() {
		return nil
	}
	if err := checkInside(root, start); err != nil {
		return err
	}
	return filepath.WalkDir(start, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		name := d.Name()
		if p != start && (skipDirs[name] && d.IsDir() || strings.HasPrefix(name, ".")) {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		if d.IsDir() {
			return nil
		}
		if exclude != "" && p == exclude {
			return nil
		}
		rel, err := filepath.Rel(root, p)
		if err != nil {
			return err
		}
		if d.Type()&fs.ModeSymlink != 0 {
			target, err := os.Stat(p)
			if err != nil {
				return fmt.Errorf("%s: broken symlink", filepath.ToSlash(rel))
			}
			if target.IsDir() {
				return fmt.Errorf("%s: symlinked directories are not packed; copy the files instead", filepath.ToSlash(rel))
			}
			if err := checkInside(root, p); err != nil {
				return err
			}
		} else if !d.Type().IsRegular() {
			return nil
		}
		add(rel)
		return nil
	})
}

// buildZip writes files (sorted slash paths under root) into a zip with
// fixed timestamps and modes.
func buildZip(root string, files []string) ([]byte, error) {
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	for _, rel := range files {
		data, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
		if err != nil {
			return nil, err
		}
		hdr := &zip.FileHeader{Name: rel, Method: zip.Deflate, Modified: packMTime}
		hdr.SetMode(0o644)
		w, err := zw.CreateHeader(hdr)
		if err != nil {
			return nil, err
		}
		if _, err := w.Write(data); err != nil {
			return nil, err
		}
	}
	if err := zw.Close(); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// ReadZipManifest reads valuz-plugin.json from a packed zip without
// validating it.
func ReadZipManifest(data []byte) (map[string]any, error) {
	zr, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil && !errors.Is(err, zip.ErrInsecurePath) {
		return nil, fmt.Errorf("not a zip archive: %w", err)
	}
	raw, err := fs.ReadFile(zr, ManifestFile)
	if err != nil {
		return nil, fmt.Errorf("%s not found at the zip root", ManifestFile)
	}
	doc, err := decodeJSON(raw)
	if err != nil {
		return nil, fmt.Errorf("%s: invalid JSON: %w", ManifestFile, err)
	}
	m, ok := doc.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("%s: must be a JSON object", ManifestFile)
	}
	return m, nil
}

// SHA256Hex returns the hex sha256 of data.
func SHA256Hex(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

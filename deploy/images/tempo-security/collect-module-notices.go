// Collect copyright/license texts from the exact locally resolved Go modules.
// This is local build provenance, not a legal classification or an upload.
package main

import (
	"crypto/sha256"
	"debug/buildinfo"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
)

type module struct {
	Path, Version, Dir string
	Main               bool
	Replace            *module
}

func noticeFiles(dir string) []string {
	entries, err := os.ReadDir(dir)
	if err != nil {
		panic(err)
	}
	names := []string{}
	for _, entry := range entries {
		name, upper := entry.Name(), strings.ToUpper(entry.Name())
		if entry.Type()&os.ModeSymlink != 0 {
			continue
		}
		if entry.IsDir() {
			if upper != "LICENSES" && upper != "LICENCES" && upper != "NOTICES" {
				continue
			}
			err := filepath.WalkDir(filepath.Join(dir, name), func(path string, child os.DirEntry, walkErr error) error {
				if walkErr != nil {
					return walkErr
				}
				relative, err := filepath.Rel(dir, path)
				if err != nil {
					return err
				}
				if child.Type()&os.ModeSymlink != 0 {
					return nil
				}
				if strings.Count(relative, string(filepath.Separator)) > 4 {
					if child.IsDir() {
						return filepath.SkipDir
					}
					return nil
				}
				if !child.IsDir() {
					names = append(names, relative)
					if len(names) > 4096 {
						return fmt.Errorf("notice directory exceeds bounded collection")
					}
				}
				return nil
			})
			if err != nil {
				panic(err)
			}
			continue
		}
		if strings.HasPrefix(upper, "LICENSE") || strings.HasPrefix(upper, "LICENCE") || strings.HasPrefix(upper, "COPYING") || strings.HasPrefix(upper, "COPYRIGHT") || strings.HasPrefix(upper, "AUTHORS") || strings.HasPrefix(upper, "NOTICE") || upper == "PATENTS" {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	return names
}

func escaped(value string) string {
	var b strings.Builder
	for _, c := range value {
		if c >= 'A' && c <= 'Z' {
			b.WriteRune('!')
			b.WriteRune(c + 'a' - 'A')
		} else {
			b.WriteRune(c)
		}
	}
	return b.String()
}

func main() {
	if len(os.Args) < 3 {
		panic("expected output directory and actual built executable paths")
	}
	out := os.Args[1]
	if err := os.MkdirAll(out, 0755); err != nil {
		panic(err)
	}
	compiled := map[string]bool{}
	executables := []map[string]any{}
	for _, name := range os.Args[2:] {
		info, err := buildinfo.ReadFile(name)
		if err != nil {
			panic(err)
		}
		compiled[info.Main.Path] = true
		deps := []map[string]string{}
		for _, dep := range info.Deps {
			compiled[dep.Path] = true
			row := map[string]string{"module": dep.Path, "version": dep.Version, "go_sum_h1": dep.Sum}
			if dep.Replace != nil {
				compiled[dep.Replace.Path] = true
				row["replacement_path"] = dep.Replace.Path
				row["replacement_version"] = dep.Replace.Version
				row["replacement_sum_h1"] = dep.Replace.Sum
			}
			deps = append(deps, row)
		}
		executables = append(executables, map[string]any{"path": name, "toolchain": info.GoVersion, "main_module": info.Main.Path, "modules": deps})
	}
	cmd := exec.Command("go", "list", "-mod=readonly", "-m", "-json", "all")
	pipe, err := cmd.StdoutPipe()
	if err != nil {
		panic(err)
	}
	cmd.Stderr = os.Stderr
	if err = cmd.Start(); err != nil {
		panic(err)
	}
	d := json.NewDecoder(pipe)
	rows := []map[string]any{}
	for {
		var m module
		err = d.Decode(&m)
		if err == io.EOF {
			break
		}
		if err != nil {
			panic(err)
		}
		selected := m
		if m.Replace != nil {
			selected = *m.Replace
		}
		texts := []string{}
		textHashes := []map[string]any{}
		if selected.Dir != "" {
			for _, name := range noticeFiles(selected.Dir) {
				b, err := os.ReadFile(filepath.Join(selected.Dir, name))
				if err != nil {
					panic(err)
				}
				folder := filepath.Join(out, m.Path+"@"+m.Version)
				if err = os.MkdirAll(folder, 0755); err != nil {
					panic(err)
				}
				if err = os.MkdirAll(filepath.Dir(filepath.Join(folder, name)), 0755); err != nil {
					panic(err)
				}
				if err = os.WriteFile(filepath.Join(folder, name), b, 0644); err != nil {
					panic(err)
				}
				texts = append(texts, name)
				textHashes = append(textHashes, map[string]any{"file": name, "bytes": len(b), "sha256": fmt.Sprintf("%x", sha256.Sum256(b))})
			}
		}
		sort.Strings(texts)
		row := map[string]any{"module": m.Path, "version": m.Version, "main": m.Main, "statically_linked_module": compiled[m.Path] || compiled[selected.Path], "replacement_path": selected.Path, "replacement_version": selected.Version, "source_directory_available": selected.Dir != "", "license_text_files": texts, "license_text_hashes": textHashes}
		if selected.Version != "" && (compiled[m.Path] || compiled[selected.Path]) {
			row["covered_source_archive_url"] = "https://proxy.golang.org/" + escaped(selected.Path) + "/@v/" + escaped(selected.Version) + ".zip"
			zip := filepath.Join("/go/pkg/mod/cache/download", escaped(selected.Path), "@v", escaped(selected.Version)+".zip")
			if b, err := os.ReadFile(zip); err == nil {
				row["cached_module_zip_sha256"] = fmt.Sprintf("%x", sha256.Sum256(b))
			} else {
				panic(fmt.Errorf("linked module ZIP unavailable: %s@%s: %w", selected.Path, selected.Version, err))
			}
		}
		rows = append(rows, row)
	}
	if err = cmd.Wait(); err != nil {
		panic(err)
	}
	b, err := json.MarshalIndent(map[string]any{"scope": "Root license/copyright/AUTHORS texts and bounded LICENSES/LICENCES/NOTICES directories (depth<=4,4096files) in locally resolved locked modules; linked-module scope is independently derived from actual native executable buildinfo. This is not exhaustive legal classification or a blind source-tree scan.", "executables": executables, "modules": rows}, "", "  ")
	if err != nil {
		panic(err)
	}
	if err = os.WriteFile(filepath.Join(out, "manifest.json"), append(b, '\n'), 0644); err != nil {
		panic(err)
	}
	if err = os.WriteFile(filepath.Join(out, "SOURCE_RETRIEVAL.md"), []byte("# Covered source for executable recipients\n\n"+
		"These executables retain third-party licenses separately from the first-party project license. "+
		"For every statically linked, versioned Go module, manifest.json records an exact covered_source_archive_url, "+
		"the authenticated go.sum h1 checksum from the built executable, and the local module ZIP SHA256 when available. "+
		"Retrieve that exact ZIP to obtain the corresponding unmodified module source, including MPL-2.0 covered files "+
		"such as HashiCorp go-version and golang-lru. No fee is charged by this project for obtaining covered source.\n\n"+
		"The executable's main module comes from the exact upstream source commit/archive in the shipped build recipe "+
		"and source-lock.json (or the frozen official OCB-generated source/configuration in this repository). "+
		"The shipped go.mod/go.sum are the complete dependency metadata modifications; compiler and flags are in Dockerfile. "+
		"Loki and Tempo keep their upstream AGPL terms; generated Collector and Prometheus keep Apache and other upstream terms. "+
		"Do not describe these third-party components as first-party MIT code. Preserve this notice and all included module notices "+
		"with any separately authorized executable or image redistribution.\n"), 0644); err != nil {
		panic(err)
	}
	fmt.Printf("retained notice records for %d resolved modules\n", len(rows))
}

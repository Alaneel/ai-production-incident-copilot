package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/moby/sys/user"
)

func TestPatchedGroupFileBounds(t *testing.T) {
	const fileCap = 10 << 20
	prefix := []byte("postgres:x:70:\n")
	for _, tc := range []struct {
		name string
		size int
		wantError bool
	}{
		{"below cap", 100, false},
		{"exactly at cap", fileCap, false},
		{"past cap", fileCap + 2, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Short comment lines exercise the aggregate file cap independently
			// of the stricter, intentional one-MiB group-line cap.
			data := append(append([]byte{}, prefix...), bytes.Repeat([]byte("#\n"), (tc.size-len(prefix))/2)...)
			if len(data) < tc.size { data = append(data, '\n') }
			if len(data) != tc.size { t.Fatalf("fixture size %d != %d",len(data),tc.size) }
			path := filepath.Join(t.TempDir(),"group")
			if err := os.WriteFile(path,data,0600); err != nil { t.Fatal(err) }
			groups, err := user.ParseGroupFile(path)
			if tc.wantError {
				if err == nil || !strings.Contains(err.Error(),"file exceeds 10485760 bytes") { t.Fatalf("expected file cap, got %v",err) }
				return
			}
			if err != nil || len(groups)!=1 || groups[0].Name!="postgres" || groups[0].Gid!=70 { t.Fatalf("valid groups lost: %#v, %v",groups,err) }
		})
	}
}

func TestPatchedGroupLineBound(t *testing.T) {
	_, err := user.ParseGroup(strings.NewReader(strings.Repeat("x",2<<20)+"\n"))
	if err == nil || !strings.Contains(err.Error(),"token too long") { t.Fatalf("expected bounded long-line refusal, got %v",err) }
}

func TestPatchedGroupNonRegularFile(t *testing.T) {
	_, err := user.ParseGroupFile(t.TempDir())
	if err == nil || !strings.Contains(err.Error(),"not a regular file") { t.Fatalf("expected non-regular refusal, got %v",err) }
}

// Package fpltest provides a fake FPL API for tests. It serves the saved real
// responses in internal/fpl/testdata, so tests in any package can exercise
// the full download -> store -> query path without the internet.
//
// Only test files import this package, so it never ends up in the server binary.
package fpltest

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
)

// testdataDir is the absolute path of internal/fpl/testdata.
//
// `go test` runs each package's tests from that package's own folder, so a
// relative path would point somewhere different for every caller. Instead we
// ask the runtime where this source file lives and go from there.
func testdataDir() string {
	_, thisFile, _, _ := runtime.Caller(0)
	return filepath.Join(filepath.Dir(thisFile), "..", "fpl", "testdata")
}

// ReadFile returns the contents of a file in internal/fpl/testdata, failing
// the test if it can't be read.
func ReadFile(t testing.TB, name string) []byte {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(testdataDir(), name))
	if err != nil {
		t.Fatalf("read testdata %s: %v", name, err)
	}
	return data
}

// Server is a fake FPL API. It answers:
//
//	/bootstrap-static/       with bootstrap-static.json
//	/fixtures/               with fixtures.json
//	/element-summary/{id}/   with element-summary-{id}.json, or 404 if there's no such file
//
// It counts requests per path (Hits) and can pretend to be down (SetDown).
type Server struct {
	*httptest.Server // embedded: gives us .URL and .Close()

	mu   sync.Mutex // guards the fields below; handlers run on other goroutines
	hits map[string]int
	down bool
}

// NewServer starts a fake FPL API that is shut down when the test ends.
func NewServer(t testing.TB) *Server {
	s := &Server{hits: map[string]int{}}
	s.Server = httptest.NewServer(http.HandlerFunc(s.handle))
	t.Cleanup(s.Close)
	return s
}

// Hits returns how many requests arrived for path, e.g. "/fixtures/".
func (s *Server) Hits(path string) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.hits[path]
}

// SetDown makes every request fail with 503 Service Unavailable (down=true),
// or work again (down=false).
func (s *Server) SetDown(down bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.down = down
}

func (s *Server) handle(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	s.hits[r.URL.Path]++
	down := s.down
	s.mu.Unlock()

	if down {
		http.Error(w, "fake FPL API is down", http.StatusServiceUnavailable)
		return
	}

	// Map the URL path to a testdata file name.
	var file string
	switch {
	case r.URL.Path == "/bootstrap-static/":
		file = "bootstrap-static.json"
	case r.URL.Path == "/fixtures/":
		file = "fixtures.json"
	case strings.HasPrefix(r.URL.Path, "/element-summary/"):
		id := strings.Trim(strings.TrimPrefix(r.URL.Path, "/element-summary/"), "/")
		file = "element-summary-" + id + ".json"
	default:
		http.NotFound(w, r)
		return
	}

	data, err := os.ReadFile(filepath.Join(testdataDir(), file))
	if err != nil {
		// Like the real API for an unknown player id.
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Write(data)
}

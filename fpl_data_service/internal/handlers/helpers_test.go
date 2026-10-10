package handlers

// Helpers shared by this package's tests.
//
// The handler tests run the whole service as the real server does, minus the
// network: a temporary SQLite database, filled through the real refresh code
// from a fake FPL API that serves saved responses (internal/fpltest).

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	"squad_scout/fpl_data_service/internal/db"
	"squad_scout/fpl_data_service/internal/fpl"
	"squad_scout/fpl_data_service/internal/fpltest"
	"squad_scout/fpl_data_service/internal/refresh"
)

// newTestService returns a service whose cache already holds the saved
// bootstrap and fixtures, plus the fake API so tests can count requests or
// take it down. Player histories are downloaded on demand, as in production.
func newTestService(t *testing.T) (*FplService, *fpltest.Server) {
	return newTestServiceWithMaxAge(t, time.Hour)
}

// newTestServiceWithMaxAge is newTestService with a chosen staleness limit.
// maxAge 0 makes every request re-download, which lets tests exercise the
// "download failed, serve the cached copy" path.
func newTestServiceWithMaxAge(t *testing.T, maxAge time.Duration) (*FplService, *fpltest.Server) {
	t.Helper()
	ctx := context.Background()

	store, err := db.Open(ctx, filepath.Join(t.TempDir(), "test.db"))
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { store.Close() })

	api := fpltest.NewServer(t)
	refresher := refresh.New(store, fpl.NewClient(api.URL), maxAge)
	if err := refresher.All(ctx); err != nil {
		t.Fatalf("initial refresh: %v", err)
	}
	return NewFplService(store, refresher), api
}

// wantCode fails the test unless err is a gRPC error with the given code.
func wantCode(t *testing.T, err error, want codes.Code) {
	t.Helper()
	if got := status.Code(err); got != want {
		t.Errorf("want %v, got %v (err=%v)", want, got, err)
	}
}

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
	"squad_scout/fpl_data_service/internal/manager"
	"squad_scout/fpl_data_service/internal/refresh"
)

// newTestService returns a service whose store already holds everything the
// background refresh downloads (bootstrap, fixtures, gameweeks 1-5), plus the
// fake API so tests can count requests or take it down. Managers' teams are
// downloaded on demand, as in production.
func newTestService(t *testing.T) (*FplService, *fpltest.Server) {
	t.Helper()
	ctx := context.Background()

	store, err := db.Open(ctx, filepath.Join(t.TempDir(), "test.db"))
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { store.Close() })

	api := fpltest.NewServer(t)
	client := fpl.NewClient(api.URL)
	if err := refresh.New(store, client, time.Hour).All(ctx); err != nil {
		t.Fatalf("initial refresh: %v", err)
	}
	return NewFplService(store, manager.NewLoader(client, store)), api
}

// wantCode fails the test unless err is a gRPC error with the given code.
func wantCode(t *testing.T, err error, want codes.Code) {
	t.Helper()
	if got := status.Code(err); got != want {
		t.Errorf("want %v, got %v (err=%v)", want, got, err)
	}
}

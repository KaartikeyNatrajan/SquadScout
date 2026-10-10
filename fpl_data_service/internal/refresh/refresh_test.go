package refresh

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"squad_scout/fpl_data_service/internal/db"
	"squad_scout/fpl_data_service/internal/fpl"
	"squad_scout/fpl_data_service/internal/fpltest"
)

// newTestRefresher returns a Refresher using an empty temporary database and
// a fake FPL API, plus the fake so tests can count requests or take it down.
func newTestRefresher(t *testing.T, maxAge time.Duration) (*Refresher, *db.Store, *fpltest.Server) {
	t.Helper()
	store, err := db.Open(context.Background(), filepath.Join(t.TempDir(), "test.db"))
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { store.Close() })

	api := fpltest.NewServer(t)
	return New(store, fpl.NewClient(api.URL), maxAge), store, api
}

func TestAll_DownloadsThenSkipsWhileFresh(t *testing.T) {
	ctx := context.Background()
	r, store, api := newTestRefresher(t, time.Hour)

	// First run: nothing stored yet, so both datasets are downloaded.
	if err := r.All(ctx); err != nil {
		t.Fatalf("first All: %v", err)
	}
	// Second run straight after: both are fresh, so nothing is downloaded.
	if err := r.All(ctx); err != nil {
		t.Fatalf("second All: %v", err)
	}

	for _, path := range []string{"/bootstrap-static/", "/fixtures/"} {
		if got := api.Hits(path); got != 1 {
			t.Errorf("%s: want 1 download, got %d", path, got)
		}
	}

	// And the data really arrived.
	if fixtures, err := store.UpcomingFixtures(ctx, 1, 0); err != nil || len(fixtures) != 33 {
		t.Errorf("want 33 upcoming Arsenal fixtures, got %d (err=%v)", len(fixtures), err)
	}
}

func TestAll_RedownloadsWhenStale(t *testing.T) {
	ctx := context.Background()
	// maxAge 0: everything is stale immediately.
	r, _, api := newTestRefresher(t, 0)

	for i := 0; i < 2; i++ {
		if err := r.All(ctx); err != nil {
			t.Fatalf("All #%d: %v", i+1, err)
		}
	}
	if got := api.Hits("/bootstrap-static/"); got != 2 {
		t.Errorf("want 2 downloads, got %d", got)
	}
}

func TestAll_ReportsDownloadFailure(t *testing.T) {
	r, store, api := newTestRefresher(t, time.Hour)
	api.SetDown(true)

	if err := r.All(context.Background()); err == nil {
		t.Fatal("want an error while the API is down, got nil")
	}
	// Bootstrap failed, so fixtures (which need its teams) weren't even tried.
	if got := api.Hits("/fixtures/"); got != 0 {
		t.Errorf("want fixtures skipped after bootstrap failed, got %d requests", got)
	}
	// Nothing was recorded as synced, so the next attempt will try again.
	if _, ok, _ := store.LastSynced(context.Background(), db.DatasetBootstrap); ok {
		t.Error("a failed download must not be recorded as a sync")
	}
}

func TestPlayerHistory(t *testing.T) {
	ctx := context.Background()
	r, store, api := newTestRefresher(t, time.Hour)
	if err := r.Bootstrap(ctx); err != nil { // the player must be stored first
		t.Fatalf("Bootstrap: %v", err)
	}

	// Two requests for Saka: only the first downloads.
	for i := 0; i < 2; i++ {
		if err := r.PlayerHistory(ctx, 12); err != nil {
			t.Fatalf("PlayerHistory #%d: %v", i+1, err)
		}
	}
	if got := api.Hits("/element-summary/12/"); got != 1 {
		t.Errorf("want 1 download, got %d", got)
	}

	matches, err := store.RecentMatches(ctx, 12, 10)
	if err != nil || len(matches) != 5 {
		t.Errorf("want 5 stored matches, got %d (err=%v)", len(matches), err)
	}
}

func TestPlayerHistory_UnknownPlayer(t *testing.T) {
	ctx := context.Background()
	r, _, _ := newTestRefresher(t, time.Hour)
	if err := r.Bootstrap(ctx); err != nil {
		t.Fatalf("Bootstrap: %v", err)
	}
	// The fake API has no history for this id and answers 404.
	if err := r.PlayerHistory(ctx, 99999); err == nil {
		t.Fatal("want an error for an unknown player, got nil")
	}
}

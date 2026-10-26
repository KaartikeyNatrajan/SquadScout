package refresh

import (
	"context"
	"fmt"
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

func TestGameweeks_DownloadsEachPlayedGameweek(t *testing.T) {
	ctx := context.Background()
	r, store, api := newTestRefresher(t, time.Hour)

	if err := r.All(ctx); err != nil {
		t.Fatalf("All: %v", err)
	}
	// The saved bootstrap is from during gameweek 5: gameweeks 1-5 are played.
	for gw := 1; gw <= 5; gw++ {
		if got := api.Hits(fmt.Sprintf("/event/%d/live/", gw)); got != 1 {
			t.Errorf("gameweek %d: want 1 download, got %d", gw, got)
		}
	}
	if got := api.Hits("/event/6/live/"); got != 0 {
		t.Errorf("gameweek 6 hasn't started; want no download, got %d", got)
	}

	// And the stats are queryable: Saka's last 5 gameweeks.
	weeks, err := store.RecentGameweeks(ctx, 12, 10)
	if err != nil || len(weeks) != 5 {
		t.Errorf("want 5 stored gameweeks for Saka, got %d (err=%v)", len(weeks), err)
	}
}

func TestGameweeks_OnlyRecentOnesGoStale(t *testing.T) {
	ctx := context.Background()
	// maxAge 0: anything that can go stale is stale immediately.
	r, _, api := newTestRefresher(t, 0)

	for i := 0; i < 2; i++ {
		if err := r.All(ctx); err != nil {
			t.Fatalf("All #%d: %v", i+1, err)
		}
	}
	// Gameweeks 1-3 are final: downloaded once even though maxAge is 0.
	// Gameweeks 4 and 5 (the previous and current) are downloaded each time.
	want := map[int]int{1: 1, 2: 1, 3: 1, 4: 2, 5: 2}
	for gw, n := range want {
		if got := api.Hits(fmt.Sprintf("/event/%d/live/", gw)); got != n {
			t.Errorf("gameweek %d: want %d downloads, got %d", gw, n, got)
		}
	}
}

func TestGameweeks_RetriesAfterFailure(t *testing.T) {
	ctx := context.Background()
	r, _, api := newTestRefresher(t, time.Hour)
	if err := r.Bootstrap(ctx); err != nil {
		t.Fatalf("Bootstrap: %v", err)
	}
	if err := r.Fixtures(ctx); err != nil {
		t.Fatalf("Fixtures: %v", err)
	}

	api.SetDown(true)
	if err := r.Gameweeks(ctx); err == nil {
		t.Fatal("want an error while the API is down")
	}
	api.SetDown(false)
	if err := r.Gameweeks(ctx); err != nil {
		t.Fatalf("Gameweeks after recovery: %v", err)
	}
	// Nothing was recorded during the outage, so the next run downloads
	// everything: gameweek 1 was attempted twice, the rest once.
	if got := api.Hits("/event/1/live/"); got != 2 {
		t.Errorf("gameweek 1: want 2 attempts, got %d", got)
	}
	if got := api.Hits("/event/5/live/"); got != 1 {
		t.Errorf("gameweek 5: want 1 download, got %d", got)
	}
}

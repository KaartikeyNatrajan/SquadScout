package db

import (
	"context"
	"testing"
)

func TestUpsertFixtures(t *testing.T) {
	ctx := context.Background()
	store := openTestStore(t)
	// Teams first: fixtures reference them.
	if err := store.UpsertBootstrap(ctx, loadBootstrap(t)); err != nil {
		t.Fatalf("upsert bootstrap: %v", err)
	}

	fixtures := loadFixtures(t)
	for i := 1; i <= 2; i++ { // twice: the update path must not duplicate rows
		if err := store.UpsertFixtures(ctx, fixtures); err != nil {
			t.Fatalf("upsert #%d: %v", i, err)
		}
	}

	if got := countRows(t, store, "fixtures"); got != 380 {
		t.Errorf("fixtures: want 380, got %d", got)
	}
	if _, ok, err := store.LastSynced(ctx, DatasetFixtures); err != nil || !ok {
		t.Errorf("want fixtures sync recorded, got ok=%v err=%v", ok, err)
	}

	// 50 finished fixtures have scores; the other 330 must be NULL, not 0.
	var unplayedWithScore int
	err := store.db.QueryRow("SELECT COUNT(*) FROM fixtures WHERE finished = 0 AND home_score IS NOT NULL").Scan(&unplayedWithScore)
	if err != nil {
		t.Fatalf("count: %v", err)
	}
	if unplayedWithScore != 0 {
		t.Errorf("want no scores on unplayed fixtures, got %d", unplayedWithScore)
	}
}

func TestUpsertFixtures_NeedsTeams(t *testing.T) {
	// Without teams the foreign keys reject every fixture, and the
	// transaction means nothing at all is saved.
	store := openTestStore(t)
	if err := store.UpsertFixtures(context.Background(), loadFixtures(t)); err == nil {
		t.Fatal("want a foreign key error, got nil")
	}
	if got := countRows(t, store, "fixtures"); got != 0 {
		t.Errorf("want nothing saved after a failed upsert, got %d rows", got)
	}
}

func TestUpcomingFixtures(t *testing.T) {
	ctx := context.Background()
	store := seededStore(t)

	// Arsenal (1) have played gameweeks 1-5 in the saved data.
	got, err := store.UpcomingFixtures(ctx, 1, 3)
	if err != nil {
		t.Fatalf("UpcomingFixtures: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("want 3 fixtures, got %d", len(got))
	}

	// Next up: home to Leeds (13) in gameweek 6, then away at Nott'm Forest (18).
	first := got[0]
	if first.ID != 51 || first.Gameweek != 6 || first.HomeTeamName != "Arsenal" || first.AwayTeamName != "Leeds" {
		t.Errorf("first fixture: want 51, GW6, Arsenal v Leeds; got %+v", first)
	}
	if first.HomeDifficulty != 3 || first.AwayDifficulty != 5 || first.Finished || first.HomeScore != nil {
		t.Errorf("first fixture details: got %+v", first)
	}
	if got[1].ID != 69 || got[1].AwayTeamName != "Arsenal" || got[2].ID != 71 {
		t.Errorf("want fixtures 51, 69, 71 in order, got %d, %d, %d", got[0].ID, got[1].ID, got[2].ID)
	}

	// limit 0 means all remaining: 38 - 5 played.
	all, err := store.UpcomingFixtures(ctx, 1, 0)
	if err != nil {
		t.Fatalf("UpcomingFixtures(all): %v", err)
	}
	if len(all) != 33 {
		t.Errorf("all remaining: want 33, got %d", len(all))
	}
	for i := 1; i < len(all); i++ {
		if all[i].KickoffTime < all[i-1].KickoffTime {
			t.Errorf("not sorted by kickoff: %s after %s", all[i].KickoffTime, all[i-1].KickoffTime)
		}
	}

	// An unknown team simply has no fixtures.
	none, err := store.UpcomingFixtures(ctx, 99, 5)
	if err != nil || len(none) != 0 {
		t.Errorf("unknown team: want none, got %v (err=%v)", none, err)
	}
}

package db

import (
	"context"
	"testing"

	"squad_scout/fpl_data_service/internal/fpl"
)

// Saka (player 12) in the saved data, gameweeks 1-5:
//
//	GW  opponent        home  score  min  goals  pts  xG
//	1   Coventry City   yes   3-0    67   1      9    0.64
//	2   Aston Villa     no    1-0    90   1      11   0.63
//	3   Chelsea         yes   2-1    89   0      2    0.23
//	4   Sunderland      no    2-0    90   1      8    1.38
//	5   Brighton        no    0-3    80   0      2    0.34

// storeWithGameweeks returns a seeded store with gameweeks 1-5 saved.
func storeWithGameweeks(t *testing.T) *Store {
	t.Helper()
	store := seededStore(t)
	for gw := 1; gw <= 5; gw++ {
		if err := store.UpsertGameweekStats(context.Background(), gw, loadGameweek(t, gw)); err != nil {
			t.Fatalf("upsert gameweek %d: %v", gw, err)
		}
	}
	return store
}

func TestUpsertGameweekStats(t *testing.T) {
	ctx := context.Background()
	store := seededStore(t)
	live := loadGameweek(t, 1)

	for i := 1; i <= 2; i++ { // twice: re-saving must replace, not duplicate
		if err := store.UpsertGameweekStats(ctx, 1, live); err != nil {
			t.Fatalf("upsert #%d: %v", i, err)
		}
	}

	// Every one of the 610 players registered in gameweek 1 had a match.
	if got := countRows(t, store, "player_gameweeks"); got != 610 {
		t.Errorf("player_gameweeks: want 610 rows, got %d", got)
	}
	if got := countRows(t, store, "player_gameweek_matches"); got != 610 {
		t.Errorf("player_gameweek_matches: want 610 rows, got %d", got)
	}
	if _, ok, err := store.LastSynced(ctx, GameweekStatsDataset(1)); err != nil || !ok {
		t.Errorf("want the gameweek 1 sync recorded, got ok=%v err=%v", ok, err)
	}
}

func TestUpsertGameweekStats_ReplacesOnlyThatGameweek(t *testing.T) {
	ctx := context.Background()
	store := storeWithGameweeks(t)

	var total, inGameweek5 int
	store.db.QueryRow("SELECT COUNT(*), SUM(gameweek = 5) FROM player_gameweeks").Scan(&total, &inGameweek5)

	// Re-save gameweek 5 as empty (as if FPL had removed everything from it):
	// gameweek 5's rows go, gameweeks 1-4 stay untouched.
	if err := store.UpsertGameweekStats(ctx, 5, &fpl.EventLive{}); err != nil {
		t.Fatalf("re-upsert: %v", err)
	}
	if got := countRows(t, store, "player_gameweeks"); got != total-inGameweek5 {
		t.Errorf("want %d rows left (%d minus gameweek 5's %d), got %d", total-inGameweek5, total, inGameweek5, got)
	}
}

func TestRecentGameweeks(t *testing.T) {
	store := storeWithGameweeks(t)

	got, err := store.RecentGameweeks(context.Background(), 12, 3)
	if err != nil {
		t.Fatalf("RecentGameweeks: %v", err)
	}
	if len(got) != 3 || got[0].Gameweek != 5 || got[1].Gameweek != 4 || got[2].Gameweek != 3 {
		t.Fatalf("want gameweeks 5, 4, 3; got %+v", got)
	}

	// Gameweek 5: away at Brighton, lost 0-3, 80 minutes, 2 points.
	gw5 := got[0]
	if gw5.Minutes != 80 || gw5.TotalPoints != 2 || !approx(gw5.ExpectedGoals, 0.34) {
		t.Errorf("gameweek 5 totals: got %+v", gw5)
	}
	if len(gw5.Matches) != 1 {
		t.Fatalf("gameweek 5: want 1 match, got %d", len(gw5.Matches))
	}
	m := gw5.Matches[0]
	if m.OpponentTeamName != "Brighton" || m.WasHome || m.Minutes != 80 || m.TotalPoints != 2 {
		t.Errorf("gameweek 5 match: got %+v", m)
	}
	// FPL's score is home 3 - away 0; Saka was away, so from his side it's 0-3.
	if m.TeamScore == nil || *m.TeamScore != 0 || m.OpponentScore == nil || *m.OpponentScore != 3 {
		t.Errorf("gameweek 5 score: want 0-3, got %v-%v", m.TeamScore, m.OpponentScore)
	}

	// Asking for more than exist returns what there is.
	all, err := store.RecentGameweeks(context.Background(), 12, 10)
	if err != nil || len(all) != 5 {
		t.Errorf("want all 5 gameweeks, got %d (err=%v)", len(all), err)
	}
}

func TestPlayerGameweek(t *testing.T) {
	ctx := context.Background()
	store := storeWithGameweeks(t)

	// Gameweek 1: at home to Coventry, won 3-0, scored once, 9 points.
	gw1, ok, err := store.PlayerGameweek(ctx, 12, 1)
	if err != nil || !ok {
		t.Fatalf("gameweek 1: ok=%v err=%v", ok, err)
	}
	if gw1.GoalsScored != 1 || gw1.TotalPoints != 9 || gw1.Minutes != 67 {
		t.Errorf("gameweek 1 totals: got %+v", gw1)
	}
	if len(gw1.Matches) != 1 || gw1.Matches[0].OpponentTeamName != "Coventry City" || !gw1.Matches[0].WasHome {
		t.Errorf("gameweek 1 match: got %+v", gw1.Matches)
	}

	// Not played yet: nothing, and not an error.
	if _, ok, err := store.PlayerGameweek(ctx, 12, 30); err != nil || ok {
		t.Errorf("gameweek 30: want ok=false and no error, got ok=%v err=%v", ok, err)
	}
}

func TestRecentGameweeks_IncludesBenchWeeks(t *testing.T) {
	ctx := context.Background()
	store := storeWithGameweeks(t)

	// Find a gameweek row where the player didn't get on: it must be stored,
	// with its match, so "played 3 of the last 5" can be worked out.
	var player, gameweek int
	err := store.db.QueryRow("SELECT player_id, gameweek FROM player_gameweeks WHERE minutes = 0 LIMIT 1").Scan(&player, &gameweek)
	if err != nil {
		t.Fatalf("find a 0-minute gameweek: %v", err)
	}
	row, ok, err := store.PlayerGameweek(ctx, player, gameweek)
	if err != nil || !ok || len(row.Matches) != 1 || row.Matches[0].Minutes != 0 {
		t.Errorf("0-minute gameweek: want one match with 0 minutes, got %+v (ok=%v err=%v)", row, ok, err)
	}
}

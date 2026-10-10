package db

import (
	"context"
	"testing"
)

func TestUpsertPlayerHistory(t *testing.T) {
	ctx := context.Background()
	store := seededStore(t)
	history := loadSakaHistory(t)

	for i := 1; i <= 2; i++ { // twice: re-downloading must replace, not duplicate
		if err := store.UpsertPlayerHistory(ctx, 12, history); err != nil {
			t.Fatalf("upsert #%d: %v", i, err)
		}
	}

	if got := countRows(t, store, "player_match_stats"); got != 5 {
		t.Errorf("want 5 matches, got %d", got)
	}
	if _, ok, err := store.LastSynced(ctx, PlayerHistoryDataset(12)); err != nil || !ok {
		t.Errorf("want history sync recorded, got ok=%v err=%v", ok, err)
	}

	// A shorter history (e.g. FPL removed a match) must replace the old one.
	if err := store.UpsertPlayerHistory(ctx, 12, history[:2]); err != nil {
		t.Fatalf("upsert shorter history: %v", err)
	}
	if got := countRows(t, store, "player_match_stats"); got != 2 {
		t.Errorf("after shorter history: want 2 matches, got %d", got)
	}
}

func TestUpsertPlayerHistory_UnknownPlayer(t *testing.T) {
	// player_match_stats.player_id references players, so this must fail.
	store := seededStore(t)
	if err := store.UpsertPlayerHistory(context.Background(), 99999, loadSakaHistory(t)); err == nil {
		t.Fatal("want a foreign key error, got nil")
	}
}

func TestRecentMatches(t *testing.T) {
	ctx := context.Background()
	store := seededStore(t)
	if err := store.UpsertPlayerHistory(ctx, 12, loadSakaHistory(t)); err != nil {
		t.Fatalf("upsert: %v", err)
	}

	got, err := store.RecentMatches(ctx, 12, 3)
	if err != nil {
		t.Fatalf("RecentMatches: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("want 3 matches, got %d", len(got))
	}
	// Most recent first: gameweeks 5, 4, 3.
	if got[0].Gameweek != 5 || got[1].Gameweek != 4 || got[2].Gameweek != 3 {
		t.Errorf("want gameweeks 5, 4, 3; got %d, %d, %d", got[0].Gameweek, got[1].Gameweek, got[2].Gameweek)
	}

	// Gameweek 5: away at Brighton, lost 0-3, 80 minutes, 2 points.
	m := got[0]
	if m.OpponentTeamName != "Brighton" || m.WasHome || m.Minutes != 80 || m.TotalPoints != 2 || !approx(m.ExpectedGoals, 0.34) {
		t.Errorf("gameweek 5: got %+v", m)
	}
	// FPL says home 3 - away 0; Saka was away, so from his side it's 0-3.
	if m.TeamScore == nil || *m.TeamScore != 0 || m.OpponentScore == nil || *m.OpponentScore != 3 {
		t.Errorf("gameweek 5 score: want 0-3, got %v-%v", m.TeamScore, m.OpponentScore)
	}

	// Asking for more matches than exist returns what there is.
	all, err := store.RecentMatches(ctx, 12, 10)
	if err != nil || len(all) != 5 {
		t.Errorf("want all 5 matches, got %d (err=%v)", len(all), err)
	}
}

func TestGameweekMatches(t *testing.T) {
	ctx := context.Background()
	store := seededStore(t)
	if err := store.UpsertPlayerHistory(ctx, 12, loadSakaHistory(t)); err != nil {
		t.Fatalf("upsert: %v", err)
	}

	got, err := store.GameweekMatches(ctx, 12, 1)
	if err != nil || len(got) != 1 {
		t.Fatalf("gameweek 1: want 1 match, got %d (err=%v)", len(got), err)
	}
	// Gameweek 1: home to Coventry, won 3-0, scored once, 9 points.
	if got[0].OpponentTeamName != "Coventry City" || !got[0].WasHome || got[0].GoalsScored != 1 || got[0].TotalPoints != 9 {
		t.Errorf("gameweek 1: got %+v", got[0])
	}

	// A gameweek that hasn't been played has no matches.
	if none, err := store.GameweekMatches(ctx, 12, 30); err != nil || len(none) != 0 {
		t.Errorf("gameweek 30: want none, got %v (err=%v)", none, err)
	}
}

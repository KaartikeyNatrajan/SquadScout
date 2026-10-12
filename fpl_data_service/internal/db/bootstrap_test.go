package db

import (
	"context"
	"testing"
	"time"
)

func TestUpsertBootstrap(t *testing.T) {
	ctx := context.Background()
	store := openTestStore(t)
	b := loadBootstrap(t)

	_, ok, err := store.LastSynced(ctx, DatasetBootstrap)
	if err != nil {
		t.Fatalf("last synced: %v", err)
	}
	if ok {
		t.Fatalf("want never synced before first upsert")
	}

	// Twice: the second run takes the ON CONFLICT ... DO UPDATE path, which
	// must neither fail nor create duplicates.
	for i := 1; i <= 2; i++ {
		if err := store.UpsertBootstrap(ctx, b); err != nil {
			t.Fatalf("upsert #%d: %v", i, err)
		}
	}

	last, ok, err := store.LastSynced(ctx, DatasetBootstrap)
	if err != nil {
		t.Fatalf("last synced: %v", err)
	}
	if !ok {
		t.Fatalf("want synced after upsert")
	}
	if time.Since(last) > time.Minute {
		t.Errorf("last sync should be just now, got %s", last)
	}

	for table, want := range map[string]int{"teams": 20, "events": 38, "chips": 8, "players": 667, "sync_log": 1} {
		if got := countRows(t, store, table); got != want {
			t.Errorf("%s: want %d rows, got %d", table, want, got)
		}
	}

	// The update path must keep short_name, not overwrite it with name.
	var shortName string
	if err := store.db.QueryRow("SELECT short_name FROM teams WHERE id = 1").Scan(&shortName); err != nil {
		t.Fatalf("query team 1: %v", err)
	}
	if shortName != "ARS" {
		t.Errorf("team 1 short_name: want ARS, got %q", shortName)
	}
}

func TestUpsertBootstrap_PlayerFields(t *testing.T) {
	ctx := context.Background()
	store := openTestStore(t)
	if err := store.UpsertBootstrap(ctx, loadBootstrap(t)); err != nil {
		t.Fatalf("upsert: %v", err)
	}

	// Saka (id 12): checks the string->float conversions and that a JSON
	// null chance of playing is stored as NULL rather than 0.
	saka, ok, err := store.GetPlayer(ctx, 12)
	if err != nil || !ok {
		t.Fatalf("get Saka: ok=%v err=%v", ok, err)
	}
	if saka.GoalsScored != 3 || saka.ExpectedGoals != 3.22 || saka.NowCost != 95 || saka.ExpectedPointsNext != 5.8 {
		t.Errorf("Saka: want goals=3 xG=3.22 now_cost=95 ep_next=5.8, got %+v", saka)
	}
	if saka.ChanceOfPlayingNextRound != nil {
		t.Errorf("Saka chance of playing: want nil (no concerns), got %d", *saka.ChanceOfPlayingNextRound)
	}

	// Transfer activity since the last deadline.
	if saka.TransfersInEvent != 99910 || saka.TransfersOutEvent != 44545 {
		t.Errorf("Saka transfers: want 99910 in, 44545 out; got %d, %d", saka.TransfersInEvent, saka.TransfersOutEvent)
	}

	// White (id 10) is doubtful: status, news and a 75% chance.
	white, _, err := store.GetPlayer(ctx, 10)
	if err != nil {
		t.Fatalf("get White: %v", err)
	}
	if white.Status != "d" || white.News != "Groin injury - 75% chance of playing" {
		t.Errorf("White: want status d with news, got %q / %q", white.Status, white.News)
	}
	if white.ChanceOfPlayingNextRound == nil || *white.ChanceOfPlayingNextRound != 75 {
		t.Errorf("White chance of playing: want 75, got %v", white.ChanceOfPlayingNextRound)
	}
}

func TestNextGameweek(t *testing.T) {
	ctx := context.Background()
	store := openTestStore(t)

	// Before any download there are no gameweeks at all.
	if _, ok, err := store.NextGameweek(ctx); err != nil || ok {
		t.Fatalf("empty store: want ok=false, got ok=%v err=%v", ok, err)
	}

	if err := store.UpsertBootstrap(ctx, loadBootstrap(t)); err != nil {
		t.Fatalf("upsert: %v", err)
	}
	// The saved bootstrap is from during gameweek 5.
	gw, ok, err := store.NextGameweek(ctx)
	if err != nil || !ok || gw.ID != 6 || gw.Deadline != "2026-10-10T10:00:00Z" {
		t.Errorf("want gameweek 6 with its deadline, got %+v (ok=%v err=%v)", gw, ok, err)
	}
}

func TestChips(t *testing.T) {
	ctx := context.Background()
	store := openTestStore(t)
	if err := store.UpsertBootstrap(ctx, loadBootstrap(t)); err != nil {
		t.Fatalf("upsert: %v", err)
	}

	chips, err := store.Chips(ctx)
	if err != nil {
		t.Fatalf("Chips: %v", err)
	}
	// Two of each chip: one for gameweeks 1/2-19, one for 20-38.
	wildcards := 0
	for _, c := range chips {
		if c.Name == "wildcard" {
			wildcards++
			if c.StopEvent != 19 && c.StartEvent != 20 {
				t.Errorf("unexpected wildcard window %d-%d", c.StartEvent, c.StopEvent)
			}
		}
	}
	if len(chips) != 8 || wildcards != 2 {
		t.Errorf("want 8 allowances including 2 wildcards, got %d and %d", len(chips), wildcards)
	}
}

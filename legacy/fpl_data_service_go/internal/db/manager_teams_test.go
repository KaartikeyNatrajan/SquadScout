package db

import (
	"context"
	"testing"
	"time"
)

func TestManagerTeams(t *testing.T) {
	ctx := context.Background()
	store := openTestStore(t)

	if _, ok, err := store.LoadManagerTeam(ctx, 6); err != nil || ok {
		t.Fatalf("before saving: want ok=false, got ok=%v err=%v", ok, err)
	}

	fetched := time.Date(2026, 10, 1, 12, 0, 0, 0, time.UTC)
	deadline := time.Date(2026, 10, 10, 10, 0, 0, 0, time.UTC)
	if err := store.SaveManagerTeam(ctx, 6, []byte(`{"ID":6}`), fetched, deadline); err != nil {
		t.Fatalf("save: %v", err)
	}
	// Saving again replaces the first copy.
	if err := store.SaveManagerTeam(ctx, 6, []byte(`{"ID":6,"Bank":1}`), fetched, deadline); err != nil {
		t.Fatalf("save again: %v", err)
	}

	got, ok, err := store.LoadManagerTeam(ctx, 6)
	if err != nil || !ok {
		t.Fatalf("load: ok=%v err=%v", ok, err)
	}
	if string(got.Data) != `{"ID":6,"Bank":1}` || !got.FetchedAt.Equal(fetched) || !got.ValidUntil.Equal(deadline) {
		t.Errorf("want the second copy with its times, got %s / %v / %v", got.Data, got.FetchedAt, got.ValidUntil)
	}
	if n := countRows(t, store, "manager_teams"); n != 1 {
		t.Errorf("want 1 row, got %d", n)
	}
}

func TestCurrentGameweekAndDeadline(t *testing.T) {
	ctx := context.Background()
	store := seededStore(t)

	// The saved bootstrap is from during gameweek 5.
	if gw, ok, err := store.CurrentGameweek(ctx); err != nil || !ok || gw != 5 {
		t.Errorf("current gameweek: want 5, got %d (ok=%v err=%v)", gw, ok, err)
	}
	if d, ok, err := store.Deadline(ctx, 6); err != nil || !ok || d != "2026-10-10T10:00:00Z" {
		t.Errorf("gameweek 6 deadline: got %q (ok=%v err=%v)", d, ok, err)
	}
	if _, ok, err := store.Deadline(ctx, 39); err != nil || ok {
		t.Errorf("gameweek 39: want ok=false, got ok=%v err=%v", ok, err)
	}
}

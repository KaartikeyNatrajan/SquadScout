package manager

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"squad_scout/fpl_data_service/internal/db"
	"squad_scout/fpl_data_service/internal/fpl"
	"squad_scout/fpl_data_service/internal/fpltest"
	"squad_scout/fpl_data_service/internal/refresh"
)

// beforeDeadline is a moment before the saved data's next deadline
// (gameweek 6, 2026-10-10 10:00 UTC); afterDeadline is one after it.
var (
	beforeDeadline = time.Date(2026, 10, 1, 12, 0, 0, 0, time.UTC)
	afterDeadline  = time.Date(2026, 10, 11, 12, 0, 0, 0, time.UTC)
)

// newTestLoader returns a Loader backed by a fake FPL API and a store
// filled with the saved bootstrap (players, chip rules, deadlines), with its
// clock set to beforeDeadline.
func newTestLoader(t *testing.T) (*Loader, *db.Store, *fpltest.Server) {
	t.Helper()
	ctx := context.Background()
	store, err := db.Open(ctx, filepath.Join(t.TempDir(), "test.db"))
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { store.Close() })

	api := fpltest.NewServer(t)
	client := fpl.NewClient(api.URL)
	if err := refresh.New(store, client, time.Hour).Bootstrap(ctx); err != nil {
		t.Fatalf("bootstrap: %v", err)
	}
	loader := NewLoader(client, store)
	loader.now = func() time.Time { return beforeDeadline }
	return loader, store, api
}

func pickByPosition(team *Team, position int) Pick {
	for _, p := range team.Picks {
		if p.SquadPosition == position {
			return p
		}
	}
	return Pick{}
}

// Team 6 had a normal gameweek 5: two transfers, no chip.
func TestLoad_NormalTeam(t *testing.T) {
	loader, _, _ := newTestLoader(t)

	team, err := loader.Load(context.Background(), 6)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if team.SquadGameweek != 5 || team.NextGameweek != 6 || len(team.Picks) != 15 {
		t.Errorf("want the gameweek 5 squad of 15 for gameweek 6, got GW%d -> GW%d with %d picks",
			team.SquadGameweek, team.NextGameweek, len(team.Picks))
	}
	if team.Bank != 1 || team.FreeTransfers != 2 {
		t.Errorf("want £0.1m in the bank and 2 free transfers, got %d and %d", team.Bank, team.FreeTransfers)
	}
	if len(team.ChipsAvailable) != 1 || team.ChipsAvailable[0] != "freehit" {
		t.Errorf("want only the Free Hit left, got %v", team.ChipsAvailable)
	}

	// Haaland (position 11): captain, bought at £15.5m, now £15.6m: sells for £15.5m.
	haaland := pickByPosition(team, 11)
	if haaland.PlayerID == 0 || !haaland.IsCaptain || haaland.SellingPrice != 155 || haaland.SellingPriceIsEstimate {
		t.Errorf("Haaland: got %+v", haaland)
	}
	// Raya (position 1) has been in the squad from the start: price unknown.
	if raya := pickByPosition(team, 1); !raya.SellingPriceIsEstimate || raya.SellingPrice != 61 {
		t.Errorf("Raya: want an estimated selling price of 61, got %+v", raya)
	}
}

// Team 1 played a Free Hit in gameweek 5, so its real squad is gameweek 4's.
func TestLoad_AfterFreeHit(t *testing.T) {
	loader, _, _ := newTestLoader(t)

	team, err := loader.Load(context.Background(), 1)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if team.SquadGameweek != 4 || team.NextGameweek != 6 {
		t.Errorf("want the gameweek 4 squad for gameweek 6, got GW%d -> GW%d", team.SquadGameweek, team.NextGameweek)
	}
	// The gameweek 4 captain was Palmer; the Free Hit captain was Haaland.
	if captain := pickByPosition(team, 6); !captain.IsCaptain {
		t.Errorf("want the gameweek 4 captain (position 6), got %+v", captain)
	}
	// 1 after GW1, then +1 each week (chips keep saved transfers), capped at 5.
	if team.FreeTransfers != 5 {
		t.Errorf("want 5 free transfers, got %d", team.FreeTransfers)
	}
	found := false
	for _, n := range team.Notes {
		found = found || strings.Contains(n, "Free Hit")
	}
	if !found {
		t.Errorf("want a note explaining the Free Hit, got %v", team.Notes)
	}
}

func TestLoad_UnknownTeam(t *testing.T) {
	loader, _, _ := newTestLoader(t)

	_, err := loader.Load(context.Background(), 999)
	if !errors.Is(err, ErrTeamNotFound) {
		t.Errorf("want ErrTeamNotFound, got %v", err)
	}
}

func TestLoad_SavedUntilTheDeadline(t *testing.T) {
	ctx := context.Background()
	loader, _, api := newTestLoader(t)

	for i := 0; i < 3; i++ {
		if _, err := loader.Load(ctx, 6); err != nil {
			t.Fatalf("Load #%d: %v", i+1, err)
		}
	}
	if got := api.Hits("/entry/6/"); got != 1 {
		t.Errorf("before the deadline: want 1 download for 3 loads, got %d", got)
	}

	// Once the deadline has passed, the squad may have changed: download again.
	loader.now = func() time.Time { return afterDeadline }
	if _, err := loader.Load(ctx, 6); err != nil {
		t.Fatalf("Load after deadline: %v", err)
	}
	if got := api.Hits("/entry/6/"); got != 2 {
		t.Errorf("after the deadline: want a second download, got %d downloads", got)
	}
}

func TestLoad_SurvivesRestart(t *testing.T) {
	ctx := context.Background()
	loader, store, api := newTestLoader(t)
	if _, err := loader.Load(ctx, 6); err != nil {
		t.Fatalf("Load: %v", err)
	}

	// A new Loader on the same database, as after restarting the server.
	restarted := NewLoader(fpl.NewClient(api.URL), store)
	restarted.now = func() time.Time { return beforeDeadline }
	team, err := restarted.Load(ctx, 6)
	if err != nil {
		t.Fatalf("Load after restart: %v", err)
	}
	if got := api.Hits("/entry/6/"); got != 1 {
		t.Errorf("want the saved copy (1 download in total), got %d downloads", got)
	}
	// The saved copy is the same team, captain and all.
	if len(team.Picks) != 15 || !pickByPosition(team, 11).IsCaptain || team.FreeTransfers != 2 {
		t.Errorf("saved copy differs: %+v", team)
	}
}

func TestLoad_APIDownServesSavedCopy(t *testing.T) {
	ctx := context.Background()
	loader, _, api := newTestLoader(t)
	if _, err := loader.Load(ctx, 6); err != nil {
		t.Fatalf("Load: %v", err)
	}

	// The deadline passes and FPL goes down: the old copy, with a warning.
	loader.now = func() time.Time { return afterDeadline }
	api.SetDown(true)
	team, err := loader.Load(ctx, 6)
	if err != nil {
		t.Fatalf("Load with API down: %v", err)
	}
	found := false
	for _, n := range team.Notes {
		found = found || strings.Contains(n, "couldn't be reached")
	}
	if !found {
		t.Errorf("want a note that the copy may be out of date, got %v", team.Notes)
	}
}

func TestLoad_APIDownNothingSaved(t *testing.T) {
	loader, _, api := newTestLoader(t)
	api.SetDown(true)

	if _, err := loader.Load(context.Background(), 6); err == nil {
		t.Error("want an error when FPL is down and nothing is saved")
	}
}

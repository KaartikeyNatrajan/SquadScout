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

// newTestLoader returns a Loader backed by a fake FPL API and a store
// filled with the saved bootstrap (players and chip rules).
func newTestLoader(t *testing.T, ttl time.Duration) (*Loader, *fpltest.Server) {
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
	return NewLoader(client, store, ttl), api
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
	loader, _ := newTestLoader(t, time.Minute)

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
	loader, _ := newTestLoader(t, time.Minute)

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
	loader, _ := newTestLoader(t, time.Minute)

	_, err := loader.Load(context.Background(), 999)
	if !errors.Is(err, ErrTeamNotFound) {
		t.Errorf("want ErrTeamNotFound, got %v", err)
	}
}

func TestLoad_Caches(t *testing.T) {
	loader, api := newTestLoader(t, time.Minute)

	for i := 0; i < 3; i++ {
		if _, err := loader.Load(context.Background(), 6); err != nil {
			t.Fatalf("Load #%d: %v", i+1, err)
		}
	}
	if got := api.Hits("/entry/6/"); got != 1 {
		t.Errorf("want 1 download for 3 loads, got %d", got)
	}
}

func TestLoad_ExpiredCacheDownloadsAgain(t *testing.T) {
	loader, api := newTestLoader(t, 0) // ttl 0: always expired

	for i := 0; i < 2; i++ {
		if _, err := loader.Load(context.Background(), 6); err != nil {
			t.Fatalf("Load #%d: %v", i+1, err)
		}
	}
	if got := api.Hits("/entry/6/"); got != 2 {
		t.Errorf("want 2 downloads, got %d", got)
	}
}

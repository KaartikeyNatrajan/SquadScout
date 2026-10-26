package db

import (
	"context"
	"testing"
)

func TestSearchPlayers(t *testing.T) {
	ctx := context.Background()
	store := seededStore(t)

	// Each case: a search, and the web_name expected first.
	cases := []struct {
		search    string
		wantFirst string
	}{
		{"saka", "Saka"},         // beats Wan-Bissaka and Sakamoto on points
		{"SAKA", "Saka"},         // case doesn't matter
		{"bukayo", "Saka"},       // first names are searchable
		{"odegaard", "Ødegaard"}, // accents don't matter...
		{"Ødegaard", "Ødegaard"}, // ...in either direction
	}
	for _, tc := range cases {
		got, err := store.SearchPlayers(ctx, tc.search, 5)
		if err != nil {
			t.Fatalf("SearchPlayers(%q): %v", tc.search, err)
		}
		if len(got) == 0 || got[0].WebName != tc.wantFirst {
			t.Errorf("SearchPlayers(%q): want %s first, got %v", tc.search, tc.wantFirst, got)
		}
	}
}

func TestSearchPlayers_OrderAndLimit(t *testing.T) {
	ctx := context.Background()
	store := seededStore(t)

	got, err := store.SearchPlayers(ctx, "wilson", 3)
	if err != nil {
		t.Fatalf("SearchPlayers: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("want 3 results, got %d", len(got))
	}
	for i := 1; i < len(got); i++ {
		if got[i].TotalPoints > got[i-1].TotalPoints {
			t.Errorf("results not sorted by points: %d before %d", got[i-1].TotalPoints, got[i].TotalPoints)
		}
	}
}

func TestSearchPlayers_NoMatch(t *testing.T) {
	got, err := seededStore(t).SearchPlayers(context.Background(), "zzzz", 5)
	if err != nil || len(got) != 0 {
		t.Errorf("want no results and no error, got %v, %v", got, err)
	}
}

func TestGetPlayer(t *testing.T) {
	ctx := context.Background()
	store := seededStore(t)

	p, ok, err := store.GetPlayer(ctx, 12)
	if err != nil || !ok {
		t.Fatalf("player 12: ok=%v err=%v", ok, err)
	}
	if p.WebName != "Saka" || p.TeamName != "Arsenal" || p.Position != 3 {
		t.Errorf("player 12: want Saka, Arsenal, midfielder; got %+v", p)
	}

	if _, ok, err := store.GetPlayer(ctx, 99999); err != nil || ok {
		t.Errorf("unknown player: want ok=false and no error, got ok=%v err=%v", ok, err)
	}
}

func TestTeamExists(t *testing.T) {
	ctx := context.Background()
	store := seededStore(t)

	for id, want := range map[int]bool{1: true, 20: true, 21: false, 0: false} {
		got, err := store.TeamExists(ctx, id)
		if err != nil || got != want {
			t.Errorf("TeamExists(%d): want %v, got %v (err=%v)", id, want, got, err)
		}
	}
}

func TestPlayersByID(t *testing.T) {
	store := seededStore(t)

	got, err := store.PlayersByID(context.Background(), []int{12, 10, 99999})
	if err != nil {
		t.Fatalf("PlayersByID: %v", err)
	}
	// The unknown id is simply missing.
	if len(got) != 2 || got[12].WebName != "Saka" || got[10].WebName != "White" {
		t.Errorf("want Saka and White, got %v", got)
	}

	empty, err := store.PlayersByID(context.Background(), nil)
	if err != nil || len(empty) != 0 {
		t.Errorf("no ids: want an empty map, got %v (err=%v)", empty, err)
	}
}

func TestFindPlayers(t *testing.T) {
	ctx := context.Background()
	store := seededStore(t)

	// The best midfielders at £8.0m or less by expected points.
	got, err := store.FindPlayers(ctx, PlayerFilter{Position: 3, MaxCost: 80, Sort: SortExpectedPoints, Limit: 10})
	if err != nil {
		t.Fatalf("FindPlayers: %v", err)
	}
	if len(got) != 10 {
		t.Fatalf("want 10 players, got %d", len(got))
	}
	for i, p := range got {
		if p.Position != 3 || p.NowCost > 80 {
			t.Errorf("%s: position %d, cost %d breaks the filter", p.WebName, p.Position, p.NowCost)
		}
		if p.Status != "a" && p.Status != "d" {
			t.Errorf("%s has status %q; unavailable players should be left out", p.WebName, p.Status)
		}
		if i > 0 && p.ExpectedPointsNext > got[i-1].ExpectedPointsNext {
			t.Errorf("not sorted by expected points: %v after %v", p.ExpectedPointsNext, got[i-1].ExpectedPointsNext)
		}
	}
}

func TestFindPlayers_ExcludeAndInclude(t *testing.T) {
	ctx := context.Background()
	store := seededStore(t)

	// The best player by total points, then the same search without him.
	best, err := store.FindPlayers(ctx, PlayerFilter{Sort: SortTotalPoints, Limit: 1})
	if err != nil || len(best) != 1 {
		t.Fatalf("best player: %v (err=%v)", best, err)
	}
	next, err := store.FindPlayers(ctx, PlayerFilter{Sort: SortTotalPoints, Limit: 1, ExcludeIDs: []int{best[0].ID}})
	if err != nil || len(next) != 1 || next[0].ID == best[0].ID {
		t.Errorf("excluding %s should return someone else, got %v (err=%v)", best[0].WebName, next, err)
	}

	// Saliba (id 6) is injured: hidden by default, found when asked for.
	hidden, err := store.FindPlayers(ctx, PlayerFilter{Position: 2, Sort: SortTotalPoints, Limit: 1000})
	if err != nil {
		t.Fatalf("FindPlayers: %v", err)
	}
	all, err := store.FindPlayers(ctx, PlayerFilter{Position: 2, Sort: SortTotalPoints, Limit: 1000, IncludeUnavailable: true})
	if err != nil {
		t.Fatalf("FindPlayers: %v", err)
	}
	if containsPlayer(hidden, 6) || !containsPlayer(all, 6) {
		t.Errorf("Saliba: want hidden by default and included on request")
	}
}

func TestTopTransfers(t *testing.T) {
	ctx := context.Background()
	store := seededStore(t)

	in, err := store.TopTransfers(ctx, true, 3)
	if err != nil || len(in) != 3 {
		t.Fatalf("TopTransfers(in): %v (err=%v)", in, err)
	}
	// From the saved data: Groß was the most transferred in.
	if in[0].WebName != "Groß" || in[0].TransfersInEvent != 763845 {
		t.Errorf("want Groß first with 763845 in, got %s with %d", in[0].WebName, in[0].TransfersInEvent)
	}

	out, err := store.TopTransfers(ctx, false, 3)
	if err != nil || len(out) != 3 {
		t.Fatalf("TopTransfers(out): %v (err=%v)", out, err)
	}
	if out[0].TransfersOutEvent < out[1].TransfersOutEvent {
		t.Errorf("transfers out not sorted: %d before %d", out[0].TransfersOutEvent, out[1].TransfersOutEvent)
	}
}

func TestTeams(t *testing.T) {
	teams, err := seededStore(t).Teams(context.Background())
	if err != nil || len(teams) != 20 {
		t.Fatalf("want 20 teams, got %d (err=%v)", len(teams), err)
	}
	if teams[0].Name != "Arsenal" || teams[0].ShortName != "ARS" {
		t.Errorf("team 1: want Arsenal (ARS), got %+v", teams[0])
	}
}

func containsPlayer(players []PlayerRow, id int) bool {
	for _, p := range players {
		if p.ID == id {
			return true
		}
	}
	return false
}

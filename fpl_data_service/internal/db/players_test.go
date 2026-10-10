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

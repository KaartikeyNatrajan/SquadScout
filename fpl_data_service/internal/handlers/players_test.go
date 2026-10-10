package handlers

import (
	"context"
	"testing"

	"google.golang.org/grpc/codes"

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
)

// search calls the SearchPlayers RPC and fails the test on any error.
func search(t *testing.T, svc *FplService, name string, limit int32) []*pb.Player {
	t.Helper()
	resp, err := svc.SearchPlayers(context.Background(), &pb.SearchPlayersRequest{Name: name, Limit: limit})
	if err != nil {
		t.Fatalf("SearchPlayers(%q): %v", name, err)
	}
	return resp.GetPlayers()
}

func TestSearchPlayers_EmptyNameIsInvalid(t *testing.T) {
	// A nil store is fine: the handler rejects the name before using it.
	svc := NewFplService(nil, nil)

	_, err := svc.SearchPlayers(context.Background(), &pb.SearchPlayersRequest{Name: "  "})

	wantCode(t, err, codes.InvalidArgument)
}

func TestSearchPlayers_FindsSaka(t *testing.T) {
	svc, _ := newTestService(t)
	players := search(t, svc, "saka", 0)

	if len(players) == 0 {
		t.Fatal("want at least one player, got none")
	}
	// Best points first, so the real Saka beats Wan-Bissaka and Sakamoto.
	p := players[0]
	if p.GetWebName() != "Saka" || p.GetTeamName() != "Arsenal" {
		t.Errorf("want Saka (Arsenal), got %s (%s)", p.GetWebName(), p.GetTeamName())
	}
	if p.GetPrice() != 9.5 {
		t.Errorf("price: want 9.5, got %v", p.GetPrice())
	}
	if p.GetPosition() != pb.Position_POSITION_MIDFIELDER {
		t.Errorf("position: want MIDFIELDER, got %v", p.GetPosition())
	}
	// The fields added for the model: plain-word availability, season totals,
	// FPL's expected points.
	if p.GetAvailability() != "available" || p.GetGoalsScored() != 3 || p.GetMinutes() != 416 || p.GetExpectedPointsNext() != 5.8 {
		t.Errorf("want available, 3 goals, 416 minutes, 5.8 expected points; got %+v", p)
	}
	// No injury concerns: the optional field must be unset, not 0 ("definitely out").
	if p.ChanceOfPlayingNextRound != nil {
		t.Errorf("chance of playing: want unset, got %d", p.GetChanceOfPlayingNextRound())
	}
}

func TestSearchPlayers_DoubtfulPlayer(t *testing.T) {
	svc, _ := newTestService(t)
	players := search(t, svc, "benjamin white", 0)

	if len(players) != 1 {
		t.Fatalf("want exactly Ben White, got %d players", len(players))
	}
	p := players[0]
	if p.GetAvailability() != "doubtful" || p.GetNews() != "Groin injury - 75% chance of playing" {
		t.Errorf("want doubtful with news, got %q / %q", p.GetAvailability(), p.GetNews())
	}
	if p.ChanceOfPlayingNextRound == nil || p.GetChanceOfPlayingNextRound() != 75 {
		t.Errorf("chance of playing: want 75, got %v", p.ChanceOfPlayingNextRound)
	}
}

func TestSearchPlayers_IgnoresAccents(t *testing.T) {
	svc, _ := newTestService(t)
	players := search(t, svc, "odegaard", 0)

	if len(players) == 0 || players[0].GetWebName() != "Ødegaard" {
		t.Errorf("want Ødegaard first, got %v", players)
	}
}

func TestSearchPlayers_NoMatch(t *testing.T) {
	// No match is an empty list, not an error.
	svc, _ := newTestService(t)
	if players := search(t, svc, "zzzz", 0); len(players) != 0 {
		t.Errorf("want no players, got %d", len(players))
	}
}

func TestSearchPlayers_Limits(t *testing.T) {
	svc, _ := newTestService(t)

	// "a" matches hundreds of players, so only the limit decides the count.
	cases := []struct {
		limit int32
		want  int
	}{
		{0, defaultLimit},       // 0 = the default
		{2, 2},                  // as asked
		{100, maxSearchResults}, // capped
	}
	for _, tc := range cases {
		if got := len(search(t, svc, "a", tc.limit)); got != tc.want {
			t.Errorf("limit %d: want %d players, got %d", tc.limit, tc.want, got)
		}
	}
}

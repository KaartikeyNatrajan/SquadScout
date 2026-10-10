package handlers

import (
	"context"
	"testing"

	"google.golang.org/grpc/codes"

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
)

// In the saved data Arsenal (team 1) have played gameweeks 1-5. Their next
// three fixtures are:
//
//	51  GW6  Arsenal v Leeds           home difficulty 3, away 5
//	69  GW7  Nott'm Forest v Arsenal   home difficulty 4, away 3
//	71  GW8  Arsenal v Everton         home difficulty 3, away 5

func TestGetFixtures(t *testing.T) {
	ctx := context.Background()
	svc, _ := newTestService(t)

	resp, err := svc.GetFixtures(ctx, &pb.GetFixturesRequest{TeamId: 1, NextN: 3})
	if err != nil {
		t.Fatalf("GetFixtures: %v", err)
	}
	fixtures := resp.GetFixtures()
	if len(fixtures) != 3 {
		t.Fatalf("want 3 fixtures, got %d", len(fixtures))
	}

	f := fixtures[0]
	if f.GetId() != 51 || f.GetGameweek() != 6 || f.GetHomeTeamName() != "Arsenal" || f.GetAwayTeamName() != "Leeds" {
		t.Errorf("first fixture: want 51, GW6, Arsenal v Leeds; got %+v", f)
	}
	if f.GetHomeDifficulty() != 3 || f.GetAwayDifficulty() != 5 || f.GetKickoffTime() != "2026-10-10T11:30:00Z" {
		t.Errorf("first fixture details: got %+v", f)
	}
	// Not played yet, so no score at all (unset, not 0-0).
	if f.HomeScore != nil || f.AwayScore != nil {
		t.Errorf("unplayed fixture has a score: %v-%v", f.HomeScore, f.AwayScore)
	}
}

func TestGetFixtures_AllRemaining(t *testing.T) {
	svc, _ := newTestService(t)

	// next_n 0 means every remaining fixture: 38 - 5 played.
	resp, err := svc.GetFixtures(context.Background(), &pb.GetFixturesRequest{TeamId: 1})
	if err != nil {
		t.Fatalf("GetFixtures: %v", err)
	}
	if got := len(resp.GetFixtures()); got != 33 {
		t.Errorf("want 33 fixtures, got %d", got)
	}
}

func TestGetFixtures_BadTeam(t *testing.T) {
	ctx := context.Background()
	svc, _ := newTestService(t)

	_, err := svc.GetFixtures(ctx, &pb.GetFixturesRequest{TeamId: 0})
	wantCode(t, err, codes.InvalidArgument)

	_, err = svc.GetFixtures(ctx, &pb.GetFixturesRequest{TeamId: 99})
	wantCode(t, err, codes.NotFound)
}

func TestGetPlayerFixtures(t *testing.T) {
	svc, _ := newTestService(t)

	resp, err := svc.GetPlayerFixtures(context.Background(), &pb.GetPlayerFixturesRequest{PlayerId: 12})
	if err != nil {
		t.Fatalf("GetPlayerFixtures: %v", err)
	}
	if resp.GetWebName() != "Saka" || resp.GetTeamName() != "Arsenal" {
		t.Errorf("want Saka (Arsenal), got %s (%s)", resp.GetWebName(), resp.GetTeamName())
	}
	if resp.GetNextGameweek() != 6 || resp.GetExpectedPointsNext() != 5.8 {
		t.Errorf("want next gameweek 6 with 5.8 expected points, got %d / %v", resp.GetNextGameweek(), resp.GetExpectedPointsNext())
	}

	fixtures := resp.GetFixtures()
	if len(fixtures) != defaultLimit {
		t.Fatalf("want %d fixtures (the default), got %d", defaultLimit, len(fixtures))
	}
	// Seen from Arsenal's side: at home to Leeds, then away at Forest. The
	// difficulty is Arsenal's half of the fixture both times.
	want := []struct {
		opponent   string
		isHome     bool
		difficulty int32
	}{
		{"Leeds", true, 3},
		{"Nott'm Forest", false, 3},
	}
	for i, w := range want {
		f := fixtures[i]
		if f.GetOpponentTeamName() != w.opponent || f.GetIsHome() != w.isHome || f.GetDifficulty() != w.difficulty {
			t.Errorf("fixture %d: want %s, home=%v, difficulty %d; got %+v", i, w.opponent, w.isHome, w.difficulty, f)
		}
	}
}

func TestGetPlayerFixtures_Errors(t *testing.T) {
	ctx := context.Background()
	svc, _ := newTestService(t)

	_, err := svc.GetPlayerFixtures(ctx, &pb.GetPlayerFixturesRequest{PlayerId: 0})
	wantCode(t, err, codes.InvalidArgument)

	_, err = svc.GetPlayerFixtures(ctx, &pb.GetPlayerFixturesRequest{PlayerId: 99999})
	wantCode(t, err, codes.NotFound)
}

func TestGetPlayerFixtures_LimitIsCapped(t *testing.T) {
	svc, _ := newTestService(t)

	resp, err := svc.GetPlayerFixtures(context.Background(), &pb.GetPlayerFixturesRequest{PlayerId: 12, NextN: 100})
	if err != nil {
		t.Fatalf("GetPlayerFixtures: %v", err)
	}
	if got := len(resp.GetFixtures()); got != maxFixtures {
		t.Errorf("want %d fixtures (the cap), got %d", maxFixtures, got)
	}
}

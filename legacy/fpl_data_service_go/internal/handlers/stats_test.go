package handlers

import (
	"context"
	"testing"

	"google.golang.org/grpc/codes"

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
	"squad_scout/fpl_data_service/internal/db"
)

// Saka's (player 12) saved gameweeks 1-5:
//
//	GW  opponent        home  score  min  goals  pts  xG
//	1   Coventry City   yes   3-0    67   1      9    0.64
//	2   Aston Villa     no    1-0    90   1      11   0.63
//	3   Chelsea         yes   2-1    89   0      2    0.23
//	4   Sunderland      no    2-0    90   1      8    1.38
//	5   Brighton        no    0-3    80   0      2    0.34

func TestGetPlayerRecentForm(t *testing.T) {
	svc, _ := newTestService(t)

	resp, err := svc.GetPlayerRecentForm(context.Background(), &pb.GetPlayerRecentFormRequest{PlayerId: 12, LastN: 3})
	if err != nil {
		t.Fatalf("GetPlayerRecentForm: %v", err)
	}
	if resp.GetWebName() != "Saka" {
		t.Errorf("want Saka, got %s", resp.GetWebName())
	}

	// The last 3 gameweeks, most recent first.
	weeks := resp.GetMatches()
	if len(weeks) != 3 || weeks[0].GetGameweek() != 5 || weeks[2].GetGameweek() != 3 {
		t.Fatalf("want gameweeks 5, 4, 3; got %v", weeks)
	}
	// One match that week, so its details are also on the gameweek itself.
	gw5 := weeks[0]
	if gw5.GetOpponentTeamName() != "Brighton" || gw5.GetWasHome() || gw5.GetTeamScore() != 0 || gw5.GetOpponentScore() != 3 {
		t.Errorf("gameweek 5: want away at Brighton, lost 0-3; got %+v", gw5)
	}
	if len(gw5.GetMatchResults()) != 1 || gw5.GetMatchResults()[0].GetMinutes() != 80 {
		t.Errorf("gameweek 5: want one match result with 80 minutes, got %v", gw5.GetMatchResults())
	}

	// Totals over gameweeks 3-5: 259 minutes, 1 goal, 12 points, 1.95 xG.
	s := resp.GetSummary()
	if s.GetMatches() != 3 || s.GetAppearances() != 3 || s.GetMinutes() != 259 || s.GetGoalsScored() != 1 || s.GetTotalPoints() != 12 {
		t.Errorf("summary counts: got %+v", s)
	}
	if s.GetExpectedGoals() != 1.95 || s.GetPointsPerMatch() != 4 {
		t.Errorf("summary: want xG 1.95 and 4 points per gameweek, got %v / %v", s.GetExpectedGoals(), s.GetPointsPerMatch())
	}
}

// Player stats come from the database alone: no FPL request is made, so
// they keep working while FPL is down.
func TestGetPlayerRecentForm_NeedsNoFPL(t *testing.T) {
	svc, api := newTestService(t)
	api.SetDown(true)
	before := api.Hits("/event/5/live/")

	resp, err := svc.GetPlayerRecentForm(context.Background(), &pb.GetPlayerRecentFormRequest{PlayerId: 12})
	if err != nil {
		t.Fatalf("GetPlayerRecentForm with FPL down: %v", err)
	}
	if got := len(resp.GetMatches()); got != defaultLimit {
		t.Errorf("want the default %d gameweeks, got %d", defaultLimit, got)
	}
	if after := api.Hits("/event/5/live/"); after != before {
		t.Errorf("want no FPL request, got %d", after-before)
	}
}

func TestGetPlayerRecentForm_UnknownPlayer(t *testing.T) {
	svc, _ := newTestService(t)

	_, err := svc.GetPlayerRecentForm(context.Background(), &pb.GetPlayerRecentFormRequest{PlayerId: 99999})
	wantCode(t, err, codes.NotFound)
}

func TestGetPlayerGameweekStats(t *testing.T) {
	svc, _ := newTestService(t)

	resp, err := svc.GetPlayerGameweekStats(context.Background(), &pb.GetPlayerGameweekStatsRequest{PlayerId: 12, Gameweek: 1})
	if err != nil {
		t.Fatalf("GetPlayerGameweekStats: %v", err)
	}
	s := resp.GetStats()
	if s.GetOpponentTeamName() != "Coventry City" || !s.GetWasHome() || s.GetGoalsScored() != 1 || s.GetTotalPoints() != 9 || s.GetMinutes() != 67 {
		t.Errorf("gameweek 1: want home to Coventry, 1 goal, 9 points, 67 minutes; got %+v", s)
	}
	if len(s.GetMatchResults()) != 1 {
		t.Errorf("want 1 match result, got %d", len(s.GetMatchResults()))
	}
	// The deprecated per-match list is no longer filled.
	if len(resp.GetMatches()) != 0 {
		t.Errorf("want the deprecated matches field empty, got %d", len(resp.GetMatches()))
	}
}

func TestGetPlayerGameweekStats_Errors(t *testing.T) {
	ctx := context.Background()
	svc, _ := newTestService(t)

	_, err := svc.GetPlayerGameweekStats(ctx, &pb.GetPlayerGameweekStatsRequest{PlayerId: 12, Gameweek: 0})
	wantCode(t, err, codes.InvalidArgument)

	_, err = svc.GetPlayerGameweekStats(ctx, &pb.GetPlayerGameweekStatsRequest{PlayerId: 99999, Gameweek: 1})
	wantCode(t, err, codes.NotFound)

	// Gameweek 30 hasn't been played yet.
	_, err = svc.GetPlayerGameweekStats(ctx, &pb.GetPlayerGameweekStatsRequest{PlayerId: 12, Gameweek: 30})
	wantCode(t, err, codes.NotFound)
}

// There's no double gameweek in the saved data yet, so these use made-up rows.
func TestToGameweekStats_DoubleGameweek(t *testing.T) {
	g := db.PlayerGameweekRow{
		PlayerID: 12, Gameweek: 7, Minutes: 150, TotalPoints: 12,
		Matches: []db.MatchRow{
			{FixtureID: 70, OpponentTeamName: "Leeds", WasHome: true, Minutes: 90, TotalPoints: 8},
			{FixtureID: 71, OpponentTeamName: "Everton", Minutes: 60, TotalPoints: 4},
		},
	}
	s := toGameweekStats(g)

	if len(s.GetMatchResults()) != 2 || s.GetMatchResults()[1].GetOpponentTeamName() != "Everton" {
		t.Errorf("want both matches in match_results, got %v", s.GetMatchResults())
	}
	// Two opponents: the single-match fields stay empty.
	if s.GetOpponentTeamName() != "" || s.GetFixtureId() != 0 {
		t.Errorf("want the single-match fields empty, got %q / %d", s.GetOpponentTeamName(), s.GetFixtureId())
	}
}

func TestSummarize(t *testing.T) {
	rows := []db.PlayerGameweekRow{
		{Minutes: 90, GoalsScored: 1, TotalPoints: 8, ExpectedGoals: 0.1},
		{Minutes: 0, TotalPoints: 0, ExpectedGoals: 0.2}, // on the bench all week
	}
	s := summarize(rows)

	if s.GetMatches() != 2 || s.GetAppearances() != 1 || s.GetTotalPoints() != 8 || s.GetPointsPerMatch() != 4 {
		t.Errorf("counts: got %+v", s)
	}
	// 0.1 + 0.2 is 0.30000000000000004 in floating point; rounding fixes that.
	if s.GetExpectedGoals() != 0.3 {
		t.Errorf("xG: want 0.3, got %v", s.GetExpectedGoals())
	}

	// No gameweeks at all: zeros, not a division by zero.
	if empty := summarize(nil); empty.GetMatches() != 0 || empty.GetPointsPerMatch() != 0 {
		t.Errorf("want an all-zero summary, got %+v", empty)
	}
}

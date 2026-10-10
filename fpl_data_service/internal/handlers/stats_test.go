package handlers

import (
	"context"
	"testing"

	"google.golang.org/grpc/codes"

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
	"squad_scout/fpl_data_service/internal/db"
)

// Saka's (player 12) saved history, gameweeks 1-5:
//
//	GW  opponent        home  score  min  goals  pts  xG
//	1   Coventry City   yes   3-0    67   1      9    0.64
//	2   Aston Villa     no    1-0    90   1      11   0.63
//	3   Chelsea         yes   2-1    89   0      2    0.23
//	4   Sunderland      no    2-0    90   1      8    1.38
//	5   Brighton        no    0-3    80   0      2    0.34

const sakaHistoryPath = "/element-summary/12/"

func TestGetPlayerRecentForm(t *testing.T) {
	svc, api := newTestService(t)

	resp, err := svc.GetPlayerRecentForm(context.Background(), &pb.GetPlayerRecentFormRequest{PlayerId: 12, LastN: 3})
	if err != nil {
		t.Fatalf("GetPlayerRecentForm: %v", err)
	}
	if resp.GetWebName() != "Saka" {
		t.Errorf("want Saka, got %s", resp.GetWebName())
	}

	// The last 3 matches, most recent first.
	matches := resp.GetMatches()
	if len(matches) != 3 || matches[0].GetGameweek() != 5 || matches[2].GetGameweek() != 3 {
		t.Fatalf("want gameweeks 5, 4, 3; got %v", matches)
	}
	m := matches[0]
	if m.GetOpponentTeamName() != "Brighton" || m.GetWasHome() || m.GetTeamScore() != 0 || m.GetOpponentScore() != 3 {
		t.Errorf("gameweek 5: want away at Brighton, lost 0-3; got %+v", m)
	}

	// Totals over gameweeks 3-5: 259 minutes, 1 goal, 12 points, 1.95 xG.
	s := resp.GetSummary()
	if s.GetMatches() != 3 || s.GetAppearances() != 3 || s.GetMinutes() != 259 || s.GetGoalsScored() != 1 || s.GetTotalPoints() != 12 {
		t.Errorf("summary counts: got %+v", s)
	}
	if s.GetExpectedGoals() != 1.95 || s.GetPointsPerMatch() != 4 {
		t.Errorf("summary: want xG 1.95 and 4 points per match, got %v / %v", s.GetExpectedGoals(), s.GetPointsPerMatch())
	}

	if got := api.Hits(sakaHistoryPath); got != 1 {
		t.Errorf("want history downloaded once, got %d", got)
	}
}

func TestGetPlayerRecentForm_UsesCacheWhileFresh(t *testing.T) {
	ctx := context.Background()
	svc, api := newTestService(t)

	for i := 0; i < 3; i++ {
		if _, err := svc.GetPlayerRecentForm(ctx, &pb.GetPlayerRecentFormRequest{PlayerId: 12}); err != nil {
			t.Fatalf("call %d: %v", i+1, err)
		}
	}
	if got := api.Hits(sakaHistoryPath); got != 1 {
		t.Errorf("want 1 download for 3 calls, got %d", got)
	}
}

func TestGetPlayerRecentForm_DefaultLength(t *testing.T) {
	svc, _ := newTestService(t)

	resp, err := svc.GetPlayerRecentForm(context.Background(), &pb.GetPlayerRecentFormRequest{PlayerId: 12})
	if err != nil {
		t.Fatalf("GetPlayerRecentForm: %v", err)
	}
	// The default is 5, which is exactly Saka's whole saved history.
	if got := len(resp.GetMatches()); got != defaultLimit {
		t.Errorf("want %d matches, got %d", defaultLimit, got)
	}
}

func TestGetPlayerRecentForm_UnknownPlayer(t *testing.T) {
	svc, api := newTestService(t)

	_, err := svc.GetPlayerRecentForm(context.Background(), &pb.GetPlayerRecentFormRequest{PlayerId: 99999})
	wantCode(t, err, codes.NotFound)

	// The id is checked against the cache first, so FPL is never asked.
	if got := api.Hits("/element-summary/99999/"); got != 0 {
		t.Errorf("want no download for an unknown player, got %d", got)
	}
}

func TestGetPlayerRecentForm_APIDownNothingCached(t *testing.T) {
	svc, api := newTestService(t)
	api.SetDown(true)

	_, err := svc.GetPlayerRecentForm(context.Background(), &pb.GetPlayerRecentFormRequest{PlayerId: 12})
	wantCode(t, err, codes.Unavailable)
}

func TestGetPlayerRecentForm_APIDownServesCache(t *testing.T) {
	ctx := context.Background()
	// maxAge 0: every call tries to download again.
	svc, api := newTestServiceWithMaxAge(t, 0)

	if _, err := svc.GetPlayerRecentForm(ctx, &pb.GetPlayerRecentFormRequest{PlayerId: 12}); err != nil {
		t.Fatalf("first call: %v", err)
	}
	api.SetDown(true)

	// The download fails now, but the copy from the first call is served.
	resp, err := svc.GetPlayerRecentForm(ctx, &pb.GetPlayerRecentFormRequest{PlayerId: 12})
	if err != nil {
		t.Fatalf("second call with API down: %v", err)
	}
	if got := len(resp.GetMatches()); got != 5 {
		t.Errorf("want 5 cached matches, got %d", got)
	}
}

func TestGetPlayerGameweekStats(t *testing.T) {
	svc, _ := newTestService(t)

	resp, err := svc.GetPlayerGameweekStats(context.Background(), &pb.GetPlayerGameweekStatsRequest{PlayerId: 12, Gameweek: 1})
	if err != nil {
		t.Fatalf("GetPlayerGameweekStats: %v", err)
	}
	if len(resp.GetMatches()) != 1 {
		t.Fatalf("want 1 match, got %d", len(resp.GetMatches()))
	}
	// One match, so the totals are that match, opponent included.
	s := resp.GetStats()
	if s.GetOpponentTeamName() != "Coventry City" || !s.GetWasHome() || s.GetGoalsScored() != 1 || s.GetTotalPoints() != 9 || s.GetMinutes() != 67 {
		t.Errorf("gameweek 1: want home to Coventry, 1 goal, 9 points, 67 minutes; got %+v", s)
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

// There's no double gameweek in the saved data yet, so the adding-up is
// tested directly with two made-up matches.
func TestSumMatches_DoubleGameweek(t *testing.T) {
	rows := []db.MatchRow{
		{OpponentTeamName: "Leeds", Minutes: 90, GoalsScored: 1, TotalPoints: 8, ExpectedGoals: 0.1},
		{OpponentTeamName: "Everton", Minutes: 30, Assists: 1, TotalPoints: 4, ExpectedGoals: 0.2},
	}

	total := sumMatches(rows)

	if total.Minutes != 120 || total.GoalsScored != 1 || total.Assists != 1 || total.TotalPoints != 12 {
		t.Errorf("totals: got %+v", total)
	}
	// 0.1 + 0.2 is 0.30000000000000004 in floating point; rounding fixes that.
	if total.ExpectedGoals != 0.3 {
		t.Errorf("xG: want 0.3, got %v", total.ExpectedGoals)
	}
	// Two opponents can't be added up, so the opponent stays empty.
	if total.OpponentTeamName != "" {
		t.Errorf("opponent: want empty, got %q", total.OpponentTeamName)
	}
}

func TestSummarize_NoMatches(t *testing.T) {
	// A player with no matches yet must get zeros, not a division by zero.
	s := summarize(nil)
	if s.GetMatches() != 0 || s.GetPointsPerMatch() != 0 {
		t.Errorf("want an all-zero summary, got %+v", s)
	}
}

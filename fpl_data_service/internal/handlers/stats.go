package handlers

import (
	"context"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
	"squad_scout/fpl_data_service/internal/db"
)

// GetPlayerRecentForm returns a player's last few matches, most recent
// first, plus totals over them. The match history is downloaded from FPL the
// first time a player is asked about, and again once it's an hour old.
func (s *FplService) GetPlayerRecentForm(ctx context.Context, req *pb.GetPlayerRecentFormRequest) (*pb.GetPlayerRecentFormResponse, error) {
	player, err := s.lookupPlayer(ctx, req.GetPlayerId())
	if err != nil {
		return nil, err
	}
	if err := s.refreshHistory(ctx, player.ID); err != nil {
		return nil, err
	}

	rows, err := s.store.RecentMatches(ctx, player.ID, clampLimit(req.GetLastN(), maxMatches))
	if err != nil {
		return nil, internalError("get recent matches", err)
	}

	// A player who hasn't had a match yet (e.g. just signed) gets an empty
	// list and zero totals, not an error.
	matches := make([]*pb.GameweekStats, 0, len(rows))
	for _, r := range rows {
		matches = append(matches, toGameweekStats(r))
	}
	return &pb.GetPlayerRecentFormResponse{
		PlayerId: int32(player.ID),
		WebName:  player.WebName,
		Matches:  matches,
		Summary:  summarize(rows),
	}, nil
}

// GetPlayerGameweekStats returns a player's stats for one gameweek: the
// totals, and each match separately (two in a double gameweek).
func (s *FplService) GetPlayerGameweekStats(ctx context.Context, req *pb.GetPlayerGameweekStatsRequest) (*pb.GetPlayerGameweekStatsResponse, error) {
	gameweek := int(req.GetGameweek())
	if gameweek <= 0 {
		return nil, status.Error(codes.InvalidArgument, "gameweek must be positive")
	}
	player, err := s.lookupPlayer(ctx, req.GetPlayerId())
	if err != nil {
		return nil, err
	}
	if err := s.refreshHistory(ctx, player.ID); err != nil {
		return nil, err
	}

	rows, err := s.store.GameweekMatches(ctx, player.ID, gameweek)
	if err != nil {
		return nil, internalError("get gameweek matches", err)
	}
	if len(rows) == 0 {
		return nil, status.Errorf(codes.NotFound,
			"%s has no match in gameweek %d (not played yet, a blank gameweek, or before he joined)",
			player.WebName, gameweek)
	}

	matches := make([]*pb.GameweekStats, 0, len(rows))
	for _, r := range rows {
		matches = append(matches, toGameweekStats(r))
	}

	// In a normal gameweek the total is simply the one match, opponent and
	// all. With two matches, add them up; opponent fields stay empty because
	// two opponents can't be summed.
	total := matches[0]
	if len(rows) > 1 {
		total = toGameweekStats(sumMatches(rows))
		total.PlayerId = int32(player.ID)
		total.Gameweek = int32(gameweek)
	}
	return &pb.GetPlayerGameweekStatsResponse{Stats: total, Matches: matches}, nil
}

// sumMatches adds up the countable stats of several matches. Fields that
// identify a match (fixture, opponent, score, kickoff) are left empty.
func sumMatches(rows []db.MatchRow) db.MatchRow {
	var t db.MatchRow
	for _, m := range rows {
		t.Minutes += m.Minutes
		t.Starts += m.Starts
		t.GoalsScored += m.GoalsScored
		t.Assists += m.Assists
		t.CleanSheets += m.CleanSheets
		t.GoalsConceded += m.GoalsConceded
		t.Saves += m.Saves
		t.Bonus += m.Bonus
		t.YellowCards += m.YellowCards
		t.RedCards += m.RedCards
		t.TotalPoints += m.TotalPoints
		t.ExpectedGoals += m.ExpectedGoals
		t.ExpectedAssists += m.ExpectedAssists
		t.ExpectedGoalInvolvements += m.ExpectedGoalInvolvements
	}
	t.ExpectedGoals = round2(t.ExpectedGoals)
	t.ExpectedAssists = round2(t.ExpectedAssists)
	t.ExpectedGoalInvolvements = round2(t.ExpectedGoalInvolvements)
	return t
}

// summarize builds the FormSummary for a run of matches.
func summarize(rows []db.MatchRow) *pb.FormSummary {
	t := sumMatches(rows)

	appearances := 0
	for _, m := range rows {
		if m.Minutes > 0 {
			appearances++
		}
	}
	// Guard against dividing by zero when there are no matches.
	pointsPerMatch := 0.0
	if len(rows) > 0 {
		pointsPerMatch = round2(float64(t.TotalPoints) / float64(len(rows)))
	}

	return &pb.FormSummary{
		Matches:         int32(len(rows)),
		Appearances:     int32(appearances),
		Minutes:         int32(t.Minutes),
		GoalsScored:     int32(t.GoalsScored),
		Assists:         int32(t.Assists),
		CleanSheets:     int32(t.CleanSheets),
		Bonus:           int32(t.Bonus),
		TotalPoints:     int32(t.TotalPoints),
		ExpectedGoals:   t.ExpectedGoals,
		ExpectedAssists: t.ExpectedAssists,
		PointsPerMatch:  pointsPerMatch,
	}
}

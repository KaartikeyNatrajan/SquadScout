package handlers

import (
	"context"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
	"squad_scout/fpl_data_service/internal/db"
)

// GetPlayerRecentForm returns a player's last few gameweeks, most recent
// first, plus totals over them. Answered from SQLite: every gameweek's stats
// are downloaded by the background refresh.
func (s *FplService) GetPlayerRecentForm(ctx context.Context, req *pb.GetPlayerRecentFormRequest) (*pb.GetPlayerRecentFormResponse, error) {
	player, err := s.lookupPlayer(ctx, req.GetPlayerId())
	if err != nil {
		return nil, err
	}

	rows, err := s.store.RecentGameweeks(ctx, player.ID, clampLimit(req.GetLastN(), maxMatches))
	if err != nil {
		return nil, internalError("get recent gameweeks", err)
	}

	// A player with no gameweeks yet (e.g. just signed) gets an empty list
	// and zero totals, not an error.
	weeks := make([]*pb.GameweekStats, 0, len(rows))
	for _, r := range rows {
		weeks = append(weeks, toGameweekStats(r))
	}
	return &pb.GetPlayerRecentFormResponse{
		PlayerId: int32(player.ID),
		WebName:  player.WebName,
		Matches:  weeks,
		Summary:  summarize(rows),
	}, nil
}

// GetPlayerGameweekStats returns a player's stats for one gameweek, with
// each match his club played that week in stats.match_results.
func (s *FplService) GetPlayerGameweekStats(ctx context.Context, req *pb.GetPlayerGameweekStatsRequest) (*pb.GetPlayerGameweekStatsResponse, error) {
	gameweek := int(req.GetGameweek())
	if gameweek <= 0 {
		return nil, status.Error(codes.InvalidArgument, "gameweek must be positive")
	}
	player, err := s.lookupPlayer(ctx, req.GetPlayerId())
	if err != nil {
		return nil, err
	}

	row, ok, err := s.store.PlayerGameweek(ctx, player.ID, gameweek)
	if err != nil {
		return nil, internalError("get gameweek", err)
	}
	if !ok {
		return nil, status.Errorf(codes.NotFound,
			"%s has no stats for gameweek %d (not played yet, a blank gameweek, or before he joined)",
			player.WebName, gameweek)
	}
	return &pb.GetPlayerGameweekStatsResponse{Stats: toGameweekStats(row)}, nil
}

// summarize builds the FormSummary for a run of gameweeks.
func summarize(rows []db.PlayerGameweekRow) *pb.FormSummary {
	var sum pb.FormSummary
	var xG, xA float64
	for _, g := range rows {
		if g.Minutes > 0 {
			sum.Appearances++
		}
		sum.Minutes += int32(g.Minutes)
		sum.GoalsScored += int32(g.GoalsScored)
		sum.Assists += int32(g.Assists)
		sum.CleanSheets += int32(g.CleanSheets)
		sum.Bonus += int32(g.Bonus)
		sum.TotalPoints += int32(g.TotalPoints)
		xG += g.ExpectedGoals
		xA += g.ExpectedAssists
	}
	sum.Matches = int32(len(rows))
	sum.ExpectedGoals = round2(xG) // a sum of floats, see round2
	sum.ExpectedAssists = round2(xA)
	// Guard against dividing by zero when there are no gameweeks.
	if len(rows) > 0 {
		sum.PointsPerMatch = round2(float64(sum.TotalPoints) / float64(len(rows)))
	}
	return &sum
}

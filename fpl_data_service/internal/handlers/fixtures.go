package handlers

import (
	"context"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
)

// GetFixtures returns a team's next fixtures, soonest first.
// next_n = 0 means all remaining fixtures (as the proto documents).
func (s *FplService) GetFixtures(ctx context.Context, req *pb.GetFixturesRequest) (*pb.GetFixturesResponse, error) {
	teamID := int(req.GetTeamId())
	if teamID <= 0 {
		return nil, status.Error(codes.InvalidArgument, "team_id must be positive")
	}

	// Check the team exists, so a wrong id is NotFound rather than an empty
	// list that looks like "no fixtures left".
	exists, err := s.store.TeamExists(ctx, teamID)
	if err != nil {
		return nil, internalError("check team", err)
	}
	if !exists {
		return nil, status.Errorf(codes.NotFound, "no team with id %d; team ids come from SearchPlayers results", teamID)
	}

	rows, err := s.store.UpcomingFixtures(ctx, teamID, int(req.GetNextN()))
	if err != nil {
		return nil, internalError("get fixtures", err)
	}

	fixtures := make([]*pb.Fixture, 0, len(rows))
	for _, r := range rows {
		fixtures = append(fixtures, toFixture(r))
	}
	return &pb.GetFixturesResponse{Fixtures: fixtures}, nil
}

// GetPlayerFixtures returns a player's next fixtures from his team's side,
// plus FPL's expected points for him in the next gameweek.
//
// FPL only publishes expected points for the next gameweek (ep_next), not
// per future fixture, so that's the one projection we pass on. The
// difficulty of each fixture is the best guide beyond that.
func (s *FplService) GetPlayerFixtures(ctx context.Context, req *pb.GetPlayerFixturesRequest) (*pb.GetPlayerFixturesResponse, error) {
	player, err := s.lookupPlayer(ctx, req.GetPlayerId())
	if err != nil {
		return nil, err // already a gRPC status error
	}
	limit := clampLimit(req.GetNextN(), maxFixtures)

	rows, err := s.store.UpcomingFixtures(ctx, player.TeamID, limit)
	if err != nil {
		return nil, internalError("get player fixtures", err)
	}
	// Once the season is over there's no next gameweek; 0 says so.
	nextGW, _, err := s.store.NextGameweek(ctx)
	if err != nil {
		return nil, internalError("get next gameweek", err)
	}

	fixtures := make([]*pb.PlayerFixture, 0, len(rows))
	for _, r := range rows {
		fixtures = append(fixtures, toPlayerFixture(r, player.TeamID))
	}
	return &pb.GetPlayerFixturesResponse{
		PlayerId:           int32(player.ID),
		WebName:            player.WebName,
		TeamName:           player.TeamName,
		NextGameweek:       int32(nextGW),
		ExpectedPointsNext: player.ExpectedPointsNext,
		Fixtures:           fixtures,
	}, nil
}

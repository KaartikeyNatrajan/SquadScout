package handlers

import (
	"context"
	"strings"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
	"squad_scout/fpl_data_service/internal/db"
	"squad_scout/fpl_data_service/internal/names"
)

// GetFixtures returns a team's next fixtures, soonest first. The team is
// given by team_id or team_name. next_n = 0 means all remaining fixtures.
func (s *FplService) GetFixtures(ctx context.Context, req *pb.GetFixturesRequest) (*pb.GetFixturesResponse, error) {
	teamID, err := s.resolveTeam(ctx, req.GetTeamId(), req.GetTeamName())
	if err != nil {
		return nil, err
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

// teamAliases maps full club names people (and models) use to FPL's short
// forms, for the clubs where one isn't simply part of the other. Keys are
// normalised (lowercase, no accents).
var teamAliases = map[string]string{
	"manchester city":   "Man City",
	"manchester united": "Man Utd",
	"man united":        "Man Utd",
	"tottenham":         "Spurs",
	"tottenham hotspur": "Spurs",
	"nottingham forest": "Nott'm Forest",
}

// resolveTeam returns the id of the team a request names, by id or by
// name, or a gRPC error ready to return. An id, if given, wins.
//
// Names are matched ignoring case and accents, in this order:
//  1. the full or short name ("Arsenal", "ARS"), or an alias above;
//  2. a team whose name contains the text or is contained in it, if exactly
//     one does ("Villa" -> Aston Villa, "Brighton & Hove Albion" -> Brighton).
func (s *FplService) resolveTeam(ctx context.Context, id int32, name string) (int, error) {
	if id < 0 {
		return 0, status.Error(codes.InvalidArgument, "team_id must be positive")
	}
	if id > 0 {
		// Check the team exists, so a wrong id is NotFound rather than an
		// empty list that looks like "no fixtures left".
		exists, err := s.store.TeamExists(ctx, int(id))
		if err != nil {
			return 0, internalError("check team", err)
		}
		if !exists {
			return 0, status.Errorf(codes.NotFound, "no team with id %d; try team_name instead", id)
		}
		return int(id), nil
	}

	name = strings.TrimSpace(name)
	if name == "" {
		return 0, status.Error(codes.InvalidArgument, "team_id or team_name is required")
	}
	teams, err := s.store.Teams(ctx)
	if err != nil {
		return 0, internalError("list teams", err)
	}

	want := names.Normalize(name)
	if alias, ok := teamAliases[want]; ok {
		want = names.Normalize(alias)
	}
	var partial []db.TeamRow
	for _, t := range teams {
		full, short := names.Normalize(t.Name), names.Normalize(t.ShortName)
		if want == full || want == short {
			return t.ID, nil
		}
		if strings.Contains(full, want) || strings.Contains(want, full) {
			partial = append(partial, t)
		}
	}
	if len(partial) == 1 {
		return partial[0].ID, nil
	}

	// No single match: list the options so the caller can try again.
	var options []string
	if len(partial) > 1 {
		options = teamNames(partial)
		return 0, status.Errorf(codes.NotFound, "%q matches several teams: %s", name, strings.Join(options, ", "))
	}
	options = teamNames(teams)
	return 0, status.Errorf(codes.NotFound, "no team called %q; the teams are: %s", name, strings.Join(options, ", "))
}

func teamNames(teams []db.TeamRow) []string {
	out := make([]string, len(teams))
	for i, t := range teams {
		out[i] = t.Name
	}
	return out
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
		NextGameweek:       int32(nextGW.ID),
		ExpectedPointsNext: player.ExpectedPointsNext,
		Fixtures:           fixtures,
	}, nil
}

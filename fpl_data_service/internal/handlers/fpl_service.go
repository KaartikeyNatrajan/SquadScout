// Package handlers implements the gRPC FplDataService defined in
// proto/squadscout/v1/fpl.proto. With gRPC, "routes" are just the methods
// on this struct; the generated code does the wiring.
package handlers

import (
	"context"
	"strings"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
	"squad_scout/fpl_data_service/internal/db"
	"squad_scout/fpl_data_service/internal/fpl"
)

type FplService struct {
	pb.UnimplementedFplDataServiceServer // keeps compiling when new RPCs are added to the proto

	store *db.Store
	fpl   *fpl.Client
}

func NewFplService(store *db.Store, client *fpl.Client) *FplService {
	return &FplService{store: store, fpl: client}
}

func (s *FplService) SearchPlayers(ctx context.Context, req *pb.SearchPlayersRequest) (*pb.SearchPlayersResponse, error) {
	name := strings.TrimSpace(req.GetName())
	if name == "" {
		return nil, status.Error(codes.InvalidArgument, "name is required")
	}

	// TODO: replace with s.store.SearchPlayers(ctx, name, limit).
	// Canned response so you can test the Python -> Go wiring end to end today.
	return &pb.SearchPlayersResponse{
		Players: []*pb.Player{{
			Id:          0,
			WebName:     "Stub(" + name + ")",
			TeamName:    "Stub FC",
			Position:    pb.Position_POSITION_MIDFIELDER,
			Price:       10.0,
			TotalPoints: 0,
			Status:      "a",
		}},
	}, nil
}

func (s *FplService) GetFixtures(ctx context.Context, req *pb.GetFixturesRequest) (*pb.GetFixturesResponse, error) {
	if req.GetTeamId() <= 0 {
		return nil, status.Error(codes.InvalidArgument, "team_id must be positive")
	}
	return nil, status.Error(codes.Unimplemented, "GetFixtures not implemented yet")
}

func (s *FplService) GetPlayerGameweekStats(ctx context.Context, req *pb.GetPlayerGameweekStatsRequest) (*pb.GetPlayerGameweekStatsResponse, error) {
	if req.GetPlayerId() <= 0 || req.GetGameweek() <= 0 {
		return nil, status.Error(codes.InvalidArgument, "player_id and gameweek must be positive")
	}
	return nil, status.Error(codes.Unimplemented, "GetPlayerGameweekStats not implemented yet")
}

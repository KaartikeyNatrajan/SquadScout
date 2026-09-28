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

// FplService is our implementation of the FplDataService from fpl.proto.
// main.go registers it with the gRPC server; the generated code then calls
// these methods when a request arrives.
type FplService struct {
	// Embedding (a type with no field name) gives FplService default versions
	// of every RPC that return "Unimplemented". It keeps the code compiling
	// when new RPCs are added to the proto.
	pb.UnimplementedFplDataServiceServer

	store *db.Store
	fpl   *fpl.Client
}

// NewFplService is a constructor: Go has no classes, so a New... function
// that builds the struct is the convention.
func NewFplService(store *db.Store, client *fpl.Client) *FplService {
	return &FplService{store: store, fpl: client}
}

// SearchPlayers handles the SearchPlayers RPC. It receives the decoded
// request and returns either a response or a gRPC error with a status code
// (InvalidArgument, Unimplemented, ...). The Python tools turn such errors
// into "ERROR: ..." text for the model.
func (s *FplService) SearchPlayers(ctx context.Context, req *pb.SearchPlayersRequest) (*pb.SearchPlayersResponse, error) {
	name := strings.TrimSpace(req.GetName())
	if name == "" {
		return nil, status.Error(codes.InvalidArgument, "name is required")
	}

	// TODO: replace with s.store.SearchPlayers(ctx, name, limit).
	// Canned response so you can test the Python -> Go wiring end to end today.
	return &pb.SearchPlayersResponse{
		Players: []*pb.Player{{
			Id:                17,
			WebName:           "Saka",
			FirstName:         "Bukayo",
			SecondName:        "Saka",
			TeamId:            1,
			TeamName:          "Arsenal",
			Position:          pb.Position_POSITION_MIDFIELDER,
			Price:             10.1,
			TotalPoints:       87,
			Form:              6.2,
			SelectedByPercent: 41.3,
			Status:            "a",
		}},
	}, nil

}

// GetFixtures validates its input, then reports Unimplemented (not built yet).
func (s *FplService) GetFixtures(ctx context.Context, req *pb.GetFixturesRequest) (*pb.GetFixturesResponse, error) {
	if req.GetTeamId() <= 0 {
		return nil, status.Error(codes.InvalidArgument, "team_id must be positive")
	}
	return nil, status.Error(codes.Unimplemented, "GetFixtures not implemented yet")
}

// GetPlayerGameweekStats: same as GetFixtures, a placeholder for now.
func (s *FplService) GetPlayerGameweekStats(ctx context.Context, req *pb.GetPlayerGameweekStatsRequest) (*pb.GetPlayerGameweekStatsResponse, error) {
	if req.GetPlayerId() <= 0 || req.GetGameweek() <= 0 {
		return nil, status.Error(codes.InvalidArgument, "player_id and gameweek must be positive")
	}
	return nil, status.Error(codes.Unimplemented, "GetPlayerGameweekStats not implemented yet")
}

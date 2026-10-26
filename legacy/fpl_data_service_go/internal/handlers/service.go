// Package handlers implements the gRPC FplDataService defined in
// proto/squadscout/v1/fpl.proto. With gRPC, "routes" are just the methods
// on this struct; the generated code does the wiring.
//
// Files in this package:
//
//	service.go    the service struct and helpers shared by every RPC
//	players.go    SearchPlayers, FindPlayers
//	fixtures.go   GetFixtures, GetPlayerFixtures
//	stats.go      GetPlayerGameweekStats, GetPlayerRecentForm
//	manager.go    GetManagerTeam
//	transfers.go  GetTransferTrends, EvaluateTransfers, SuggestTransfers
//	outlook.go    attaching next fixtures to players
//	convert.go    turning database rows into protobuf messages
//
// Every RPC follows the same shape: validate the request, read from the
// store, convert to protobuf. Only GetManagerTeam (and the RPCs built on it)
// can trigger an FPL download, the first time a team is asked about each
// gameweek; everything else is answered from SQLite. Errors are returned as gRPC status codes,
// which the Python tools turn into "ERROR: ..." text for the model:
//
//	InvalidArgument     the request itself is wrong (empty name, id <= 0)
//	NotFound            a well-formed request for something that doesn't exist
//	FailedPrecondition  it exists but can't be used yet (a team with no squad)
//	Unavailable         the FPL API is down and the team isn't saved yet
//	Internal            a bug or database failure on our side
//	Unimplemented       SuggestTransfers, until the optimizer is written
package handlers

import (
	"context"
	"errors"
	"log/slog"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
	"squad_scout/fpl_data_service/internal/db"
	"squad_scout/fpl_data_service/internal/manager"
)

// Limits on list sizes. Small lists keep tool results short, which matters
// for a small model with a small context window.
const (
	defaultLimit     = 5  // when a request leaves its limit at 0
	maxSearchResults = 20 // SearchPlayers, FindPlayers, GetTransferTrends
	maxMatches       = 10 // GetPlayerRecentForm
	maxFixtures      = 10 // GetPlayerFixtures
	outlookFixtures  = 3  // next fixtures attached to each player in a list
)

// FplService is our implementation of the FplDataService from fpl.proto.
// main.go registers it with the gRPC server; the generated code then calls
// these methods when a request arrives.
type FplService struct {
	// Embedding (a type with no field name) gives FplService default versions
	// of every RPC that return "Unimplemented". It keeps the code compiling
	// when new RPCs are added to the proto.
	pb.UnimplementedFplDataServiceServer

	store    *db.Store       // everything except managers' teams; kept fresh by main.go's timer
	managers *manager.Loader // managers' teams: downloaded on first request, saved until the deadline
}

// NewFplService is a constructor: Go has no classes, so a New... function
// that builds the struct is the convention.
func NewFplService(store *db.Store, managers *manager.Loader) *FplService {
	return &FplService{store: store, managers: managers}
}

// clampLimit turns a requested list size into one we'll actually use:
// 0 (or negative) means "the default", and anything above max becomes max.
func clampLimit(requested int32, max int) int {
	n := int(requested)
	if n <= 0 {
		return defaultLimit
	}
	if n > max {
		return max
	}
	return n
}

// internalError logs err with the operation that failed and returns it as a
// gRPC Internal error.
func internalError(op string, err error) error {
	slog.Error(op, "err", err)
	return status.Errorf(codes.Internal, "%s: %v", op, err)
}

// lookupPlayer returns the player with this id, or a gRPC error ready to
// return: InvalidArgument for a bad id, NotFound for an unknown one.
func (s *FplService) lookupPlayer(ctx context.Context, id int32) (db.PlayerRow, error) {
	if id <= 0 {
		return db.PlayerRow{}, status.Error(codes.InvalidArgument, "player_id must be positive")
	}
	p, ok, err := s.store.GetPlayer(ctx, int(id))
	if err != nil {
		return db.PlayerRow{}, internalError("get player", err)
	}
	if !ok {
		// The hint tells the caller (the model) how to recover.
		return db.PlayerRow{}, status.Errorf(codes.NotFound, "no player with id %d; use SearchPlayers to find player ids", id)
	}
	return p, nil
}

// loadTeam returns a manager's team, or a gRPC error ready to return.
func (s *FplService) loadTeam(ctx context.Context, teamID int32) (*manager.Team, error) {
	if teamID <= 0 {
		return nil, status.Error(codes.InvalidArgument, "team_id must be positive")
	}
	team, err := s.managers.Load(ctx, int(teamID))
	switch {
	case err == nil:
		return team, nil
	case errors.Is(err, manager.ErrTeamNotFound):
		return nil, status.Errorf(codes.NotFound, "no FPL team with id %d; the id is the number in the URL of the manager's FPL points page", teamID)
	case errors.Is(err, manager.ErrNoSquadYet):
		return nil, status.Error(codes.FailedPrecondition, err.Error())
	default:
		// Almost always the FPL API being down or slow.
		slog.Error("load team", "team_id", teamID, "err", err)
		return nil, status.Errorf(codes.Unavailable, "team %d can't be loaded right now", teamID)
	}
}

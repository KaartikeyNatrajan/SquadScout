package handlers

import (
	"context"
	"strings"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
)

// SearchPlayers finds players by name, best total points first.
// Served entirely from the cache that the hourly refresh keeps up to date.
func (s *FplService) SearchPlayers(ctx context.Context, req *pb.SearchPlayersRequest) (*pb.SearchPlayersResponse, error) {
	name := strings.TrimSpace(req.GetName())
	if name == "" {
		return nil, status.Error(codes.InvalidArgument, "name is required")
	}
	limit := clampLimit(req.GetLimit(), maxSearchResults)

	rows, err := s.store.SearchPlayers(ctx, name, limit)
	if err != nil {
		return nil, internalError("search players", err)
	}

	// Empty, with room for len(rows) items, so append never has to resize.
	players := make([]*pb.Player, 0, len(rows))
	for _, r := range rows {
		players = append(players, toPlayer(r))
	}
	// No match is an empty list, not an error: "nobody called that" is a
	// valid answer the caller can act on.
	return &pb.SearchPlayersResponse{Players: players}, nil
}

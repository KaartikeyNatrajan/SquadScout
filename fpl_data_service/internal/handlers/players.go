package handlers

import (
	"context"
	"math"
	"strings"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
	"squad_scout/fpl_data_service/internal/db"
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

// sortByProto maps the proto's PlayerSort to the database's. UNSPECIFIED
// means expected points, the most useful default for picking transfers.
var sortByProto = map[pb.PlayerSort]db.PlayerSort{
	pb.PlayerSort_PLAYER_SORT_UNSPECIFIED:     db.SortExpectedPoints,
	pb.PlayerSort_PLAYER_SORT_EXPECTED_POINTS: db.SortExpectedPoints,
	pb.PlayerSort_PLAYER_SORT_FORM:            db.SortForm,
	pb.PlayerSort_PLAYER_SORT_TOTAL_POINTS:    db.SortTotalPoints,
	pb.PlayerSort_PLAYER_SORT_POINTS_PER_GAME: db.SortPointsPerGame,
}

// FindPlayers returns players matching the request's filters, best first,
// each with his next fixtures. It's how the agent discovers transfer
// targets it doesn't already know by name.
func (s *FplService) FindPlayers(ctx context.Context, req *pb.FindPlayersRequest) (*pb.FindPlayersResponse, error) {
	sort, ok := sortByProto[req.GetSortBy()]
	if !ok {
		return nil, status.Errorf(codes.InvalidArgument, "unknown sort_by %v", req.GetSortBy())
	}
	if req.GetMaxPrice() < 0 {
		return nil, status.Error(codes.InvalidArgument, "max_price can't be negative")
	}

	filter := db.PlayerFilter{
		Position: int(req.GetPosition()), // UNSPECIFIED is 0, which means "any"
		// £m to tenths. Round rather than truncate: the caller can send any
		// float, and a value like 8.2999999 should mean £8.3m, not £8.2m.
		MaxCost:            int(math.Round(req.GetMaxPrice() * 10)),
		IncludeUnavailable: req.GetIncludeUnavailable(),
		Sort:               sort,
		Limit:              clampLimit(req.GetLimit(), maxSearchResults),
	}

	// Leave out players the manager already owns: they can't be bought.
	if req.GetExcludeSquadOfTeamId() != 0 {
		team, err := s.loadTeam(ctx, req.GetExcludeSquadOfTeamId())
		if err != nil {
			return nil, err
		}
		for _, p := range team.Picks {
			filter.ExcludeIDs = append(filter.ExcludeIDs, p.PlayerID)
		}
	}

	rows, err := s.store.FindPlayers(ctx, filter)
	if err != nil {
		return nil, internalError("find players", err)
	}
	players, err := s.outlooks(ctx, rows)
	if err != nil {
		return nil, err
	}
	return &pb.FindPlayersResponse{Players: players}, nil
}

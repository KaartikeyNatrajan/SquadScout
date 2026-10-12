package handlers

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"strings"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
	"squad_scout/fpl_data_service/internal/db"
	"squad_scout/fpl_data_service/internal/manager"
	"squad_scout/fpl_data_service/internal/optimizer"
	"squad_scout/fpl_data_service/internal/squad"
)

// GetTransferTrends returns the players most transferred in (or out) by all
// FPL managers since the last deadline, with their form, news and fixtures,
// so the reasons behind each move are in the same response.
func (s *FplService) GetTransferTrends(ctx context.Context, req *pb.GetTransferTrendsRequest) (*pb.GetTransferTrendsResponse, error) {
	in := req.GetDirection() != pb.TransferDirection_TRANSFER_DIRECTION_OUT // UNSPECIFIED means in
	rows, err := s.store.TopTransfers(ctx, in, clampLimit(req.GetLimit(), maxSearchResults))
	if err != nil {
		return nil, internalError("get transfer trends", err)
	}
	players, err := s.outlooks(ctx, rows)
	if err != nil {
		return nil, err
	}

	// The counts are for transfers made towards the next gameweek.
	next, _, err := s.store.NextGameweek(ctx)
	if err != nil {
		return nil, internalError("get next gameweek", err)
	}
	return &pb.GetTransferTrendsResponse{Gameweek: int32(next.ID), Players: players}, nil
}

// EvaluateTransfers checks a set of transfers for a manager's team against
// every FPL rule (via the squad package, the verifier) and reports what they
// cost and what they're expected to gain.
//
// Rule breaches come back as plain-word violations with valid = false, not
// as gRPC errors: "you can't afford him" is a useful answer for the agent to
// reason about. gRPC errors are kept for requests that can't be evaluated at
// all (an unknown team or player id).
func (s *FplService) EvaluateTransfers(ctx context.Context, req *pb.EvaluateTransfersRequest) (*pb.EvaluateTransfersResponse, error) {
	team, err := s.loadTeam(ctx, req.GetTeamId())
	if err != nil {
		return nil, err
	}
	outIDs, inIDs := toInts(req.GetPlayerOutIds()), toInts(req.GetPlayerInIds())
	if len(outIDs) == 0 && len(inIDs) == 0 {
		return nil, status.Error(codes.InvalidArgument, "list at least one player out and one player in")
	}

	// Look up the squad and every player named in the request in one go.
	ids := slices.Concat(outIDs, inIDs)
	for _, p := range team.Picks {
		ids = append(ids, p.PlayerID)
	}
	players, err := s.store.PlayersByID(ctx, ids)
	if err != nil {
		return nil, internalError("look up players", err)
	}
	for _, id := range slices.Concat(outIDs, inIDs) {
		if _, ok := players[id]; !ok {
			return nil, status.Errorf(codes.NotFound, "no player with id %d; use SearchPlayers or FindPlayers to find player ids", id)
		}
	}

	// Run the transfers through the verifier. Players coming in cost their
	// current price; players in the squad are worth their selling price.
	current := currentSquad(team, players)
	var incoming []squad.Member
	for _, id := range inIDs {
		incoming = append(incoming, member(players[id], players[id].NowCost))
	}
	// The resulting squad itself isn't needed: the agent already knows the
	// squad and the swaps. Only the problems found are.
	_, violations := squad.Apply(current, outIDs, incoming)

	// Chips and the points hit.
	chip := strings.ToLower(strings.TrimSpace(req.GetChip()))
	if chip != "" {
		switch {
		case chip != squad.ChipWildcard && chip != squad.ChipFreeHit:
			violations = append(violations, fmt.Sprintf("only the wildcard or freehit chip makes transfers free, not %q", chip))
		case !slices.Contains(team.ChipsAvailable, chip):
			violations = append(violations, fmt.Sprintf("the %s chip isn't available for gameweek %d", chip, team.NextGameweek))
		}
	}
	hit := squad.HitCost(len(inIDs), team.FreeTransfers, chip)
	if req.MaxHit != nil && hit > int(req.GetMaxHit()) {
		violations = append(violations, fmt.Sprintf(
			"%d transfers with %d free cost a %d-point hit, more than the %d points allowed",
			len(inIDs), team.FreeTransfers, hit, req.GetMaxHit()))
	}

	// Expected points for the next gameweek, leaving and joining.
	var epOut, epIn float64
	for _, id := range outIDs {
		epOut += players[id].ExpectedPointsNext
	}
	for _, id := range inIDs {
		epIn += players[id].ExpectedPointsNext
	}

	playersOut, err := s.outlooks(ctx, pick(players, outIDs))
	if err != nil {
		return nil, err
	}
	playersIn, err := s.outlooks(ctx, pick(players, inIDs))
	if err != nil {
		return nil, err
	}

	notes := []string{"Expected points are FPL's projections for the next gameweek only, and ignore who would actually start."}
	if usesEstimatedPrice(team, outIDs) {
		notes = append(notes, "At least one player leaving has an estimated selling price, so bank_after may be slightly off.")
	}

	return &pb.EvaluateTransfersResponse{
		Valid:             len(violations) == 0,
		Violations:        violations,
		Transfers:         int32(len(inIDs)),
		FreeTransfers:     int32(team.FreeTransfers),
		Hit:               int32(hit),
		BankBefore:        pounds(team.Bank),
		BankAfter:         pounds(bankAfter(team, players, outIDs, inIDs)),
		ExpectedPointsOut: round2(epOut),
		ExpectedPointsIn:  round2(epIn),
		ExpectedPointsNet: round2(epIn - epOut - float64(hit)),
		PlayersOut:        playersOut,
		PlayersIn:         playersIn,
		Notes:             notes,
	}, nil
}

// SuggestTransfers will search for the best transfers. Everything around the
// search is wired up: it gathers the squad and candidate players into an
// optimizer.Request and calls optimizer.Suggest, which reports that it isn't
// implemented yet. Writing optimizer.Suggest is all that's left.
func (s *FplService) SuggestTransfers(ctx context.Context, req *pb.SuggestTransfersRequest) (*pb.SuggestTransfersResponse, error) {
	team, err := s.loadTeam(ctx, req.GetTeamId())
	if err != nil {
		return nil, err
	}
	players, err := s.squadPlayers(ctx, team)
	if err != nil {
		return nil, internalError("get squad players", err)
	}

	optReq := optimizer.Request{
		Squad:          currentSquad(team, players),
		FreeTransfers:  team.FreeTransfers,
		MaxTransfers:   int(req.GetMaxTransfers()),
		ExpectedPoints: map[int]float64{},
	}
	if optReq.MaxTransfers <= 0 {
		optReq.MaxTransfers = max(team.FreeTransfers, 1)
	}
	if req.MaxHit != nil {
		limit := int(req.GetMaxHit())
		optReq.MaxHit = &limit
	}
	for id, p := range players {
		optReq.ExpectedPoints[id] = p.ExpectedPointsNext
	}

	// Candidates: the best few available players per position not already
	// in the squad. Keeping this list short keeps the search small.
	const candidatesPerPosition = 10
	owned := make([]int, 0, len(team.Picks))
	for _, p := range team.Picks {
		owned = append(owned, p.PlayerID)
	}
	for position := 1; position <= 4; position++ {
		rows, err := s.store.FindPlayers(ctx, db.PlayerFilter{
			Position:   position,
			ExcludeIDs: owned,
			Sort:       db.SortExpectedPoints,
			Limit:      candidatesPerPosition,
		})
		if err != nil {
			return nil, internalError("find candidates", err)
		}
		for _, r := range rows {
			optReq.Candidates = append(optReq.Candidates, optimizer.Candidate{
				Member:         member(r, r.NowCost),
				ExpectedPoints: r.ExpectedPointsNext,
			})
		}
	}

	suggestions, err := optimizer.Suggest(ctx, optReq)
	if errors.Is(err, optimizer.ErrNotImplemented) {
		return nil, status.Error(codes.Unimplemented,
			"transfer suggestions aren't available yet; use FindPlayers to find targets and EvaluateTransfers to check them")
	}
	if err != nil {
		return nil, internalError("suggest transfers", err)
	}

	resp := &pb.SuggestTransfersResponse{}
	for _, sug := range suggestions {
		resp.Suggestions = append(resp.Suggestions, &pb.TransferSuggestion{
			PlayerOutIds:       toInt32s(sug.OutIDs),
			PlayerInIds:        toInt32s(sug.InIDs),
			Hit:                int32(sug.Hit),
			ExpectedPointsGain: round2(sug.ExpectedGain),
		})
	}
	return resp, nil
}

// currentSquad turns a team into the squad package's form, with each
// player valued at his selling price.
func currentSquad(team *manager.Team, players map[int]db.PlayerRow) squad.Squad {
	s := squad.Squad{Bank: team.Bank}
	for _, p := range team.Picks {
		s.Members = append(s.Members, member(players[p.PlayerID], p.SellingPrice))
	}
	return s
}

// member turns a player into a squad.Member worth price (tenths of £m).
func member(p db.PlayerRow, price int) squad.Member {
	return squad.Member{
		PlayerID: p.ID,
		Name:     p.WebName,
		Position: p.Position,
		ClubID:   p.TeamID,
		ClubName: p.TeamName,
		Price:    price,
	}
}

// bankAfter is the money left after the transfers: the bank, plus the
// selling price of each player leaving, minus the price of each joining.
// It's worked out even when the transfers break a rule, so the agent can
// see how far over budget a plan is.
func bankAfter(team *manager.Team, players map[int]db.PlayerRow, outIDs, inIDs []int) int {
	bank := team.Bank
	for _, p := range team.Picks {
		if slices.Contains(outIDs, p.PlayerID) {
			bank += p.SellingPrice
		}
	}
	for _, id := range inIDs {
		bank -= players[id].NowCost
	}
	return bank
}

// usesEstimatedPrice reports whether any player in outIDs has an estimated
// selling price.
func usesEstimatedPrice(team *manager.Team, outIDs []int) bool {
	for _, p := range team.Picks {
		if p.SellingPriceIsEstimate && slices.Contains(outIDs, p.PlayerID) {
			return true
		}
	}
	return false
}

// pick returns the players with these ids, in the same order.
func pick(players map[int]db.PlayerRow, ids []int) []db.PlayerRow {
	out := make([]db.PlayerRow, 0, len(ids))
	for _, id := range ids {
		out = append(out, players[id])
	}
	return out
}

func toInts(ids []int32) []int {
	out := make([]int, len(ids))
	for i, id := range ids {
		out[i] = int(id)
	}
	return out
}

func toInt32s(ids []int) []int32 {
	out := make([]int32, len(ids))
	for i, id := range ids {
		out[i] = int32(id)
	}
	return out
}

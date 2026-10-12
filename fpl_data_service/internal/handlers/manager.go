package handlers

import (
	"context"

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
	"squad_scout/fpl_data_service/internal/db"
	"squad_scout/fpl_data_service/internal/manager"
)

// GetManagerTeam returns a manager's squad, each player with his
// availability, expected points and next fixtures, plus the bank, free
// transfers and chips left. One call has everything needed to pick a
// captain or read the team's injury news.
func (s *FplService) GetManagerTeam(ctx context.Context, req *pb.GetManagerTeamRequest) (*pb.GetManagerTeamResponse, error) {
	team, err := s.loadTeam(ctx, req.GetTeamId())
	if err != nil {
		return nil, err
	}
	players, err := s.squadPlayers(ctx, team)
	if err != nil {
		return nil, internalError("get squad players", err)
	}

	resp := &pb.GetManagerTeamResponse{
		TeamId:         int32(team.ID),
		TeamName:       team.Name,
		ManagerName:    team.ManagerName,
		OverallPoints:  int32(team.OverallPoints),
		OverallRank:    int32(team.OverallRank),
		SquadGameweek:  int32(team.SquadGameweek),
		NextGameweek:   int32(team.NextGameweek),
		Bank:           pounds(team.Bank),
		FreeTransfers:  int32(team.FreeTransfers),
		ChipsAvailable: team.ChipsAvailable,
		Notes:          team.Notes,
	}

	cache := s.newFixtureCache()
	for _, pick := range team.Picks {
		p := players[pick.PlayerID] // manager.Load checked every pick is stored
		fixtures, err := cache.next(ctx, p.TeamID)
		if err != nil {
			return nil, internalError("get fixtures", err)
		}
		resp.SquadValue += pounds(pick.SellingPrice)
		resp.Players = append(resp.Players, &pb.SquadPlayer{
			Player:                 toPlayer(p),
			NextFixtures:           fixtures,
			SquadPosition:          int32(pick.SquadPosition),
			IsCaptain:              pick.IsCaptain,
			IsViceCaptain:          pick.IsViceCaptain,
			SellingPrice:           pounds(pick.SellingPrice),
			SellingPriceIsEstimate: pick.SellingPriceIsEstimate,
		})
	}
	resp.SquadValue = round2(resp.SquadValue) // a sum of floats, see round2

	// The deadline comes from the gameweek table. If our bootstrap is a little
	// behind the team's data they can disagree; then leave it out rather
	// than give the wrong gameweek's deadline.
	if next, ok, err := s.store.NextGameweek(ctx); err == nil && ok && next.ID == team.NextGameweek {
		resp.NextDeadline = next.Deadline
	}
	return resp, nil
}

// squadPlayers looks up every player in the team's squad, keyed by id.
func (s *FplService) squadPlayers(ctx context.Context, team *manager.Team) (map[int]db.PlayerRow, error) {
	ids := make([]int, len(team.Picks))
	for i, p := range team.Picks {
		ids[i] = p.PlayerID
	}
	return s.store.PlayersByID(ctx, ids)
}

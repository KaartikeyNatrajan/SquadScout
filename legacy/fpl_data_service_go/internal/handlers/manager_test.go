package handlers

import (
	"context"
	"testing"

	"google.golang.org/grpc/codes"

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
)

// Team 6 in the saved data: a normal gameweek 5 (two transfers, no chip),
// £0.1m in the bank, 2 free transfers for gameweek 6, only the Free Hit left,
// Haaland (squad position 11) captain. Team 1 played a Free Hit in gameweek 5.

func TestGetManagerTeam(t *testing.T) {
	svc, _ := newTestService(t)

	resp, err := svc.GetManagerTeam(context.Background(), &pb.GetManagerTeamRequest{TeamId: 6})
	if err != nil {
		t.Fatalf("GetManagerTeam: %v", err)
	}
	if resp.GetSquadGameweek() != 5 || resp.GetNextGameweek() != 6 || resp.GetNextDeadline() != "2026-10-10T10:00:00Z" {
		t.Errorf("want the gameweek 5 squad for gameweek 6 with its deadline, got GW%d -> GW%d, %q",
			resp.GetSquadGameweek(), resp.GetNextGameweek(), resp.GetNextDeadline())
	}
	if resp.GetBank() != 0.1 || resp.GetFreeTransfers() != 2 {
		t.Errorf("want £0.1m and 2 free transfers, got %v and %d", resp.GetBank(), resp.GetFreeTransfers())
	}
	if chips := resp.GetChipsAvailable(); len(chips) != 1 || chips[0] != "freehit" {
		t.Errorf("want only the Free Hit left, got %v", chips)
	}
	if len(resp.GetNotes()) == 0 {
		t.Error("want notes explaining the estimates")
	}

	players := resp.GetPlayers()
	if len(players) != 15 {
		t.Fatalf("want 15 players, got %d", len(players))
	}
	var captain *pb.SquadPlayer
	for i, p := range players {
		if p.GetSquadPosition() != int32(i+1) {
			t.Errorf("players out of order: position %d at index %d", p.GetSquadPosition(), i)
		}
		if len(p.GetNextFixtures()) != outlookFixtures {
			t.Errorf("%s: want %d next fixtures, got %d", p.GetPlayer().GetWebName(), outlookFixtures, len(p.GetNextFixtures()))
		}
		if p.GetIsCaptain() {
			captain = p
		}
	}
	if captain == nil || captain.GetPlayer().GetWebName() != "Haaland" || captain.GetSellingPrice() != 15.5 {
		t.Errorf("want Haaland as captain selling for £15.5m, got %+v", captain)
	}
}

func TestGetManagerTeam_AfterFreeHit(t *testing.T) {
	svc, _ := newTestService(t)

	resp, err := svc.GetManagerTeam(context.Background(), &pb.GetManagerTeamRequest{TeamId: 1})
	if err != nil {
		t.Fatalf("GetManagerTeam: %v", err)
	}
	// The Free Hit squad is gone; the gameweek 4 squad is back.
	if resp.GetSquadGameweek() != 4 {
		t.Errorf("want the gameweek 4 squad, got gameweek %d", resp.GetSquadGameweek())
	}
}

func TestGetManagerTeam_Errors(t *testing.T) {
	ctx := context.Background()
	svc, _ := newTestService(t)

	_, err := svc.GetManagerTeam(ctx, &pb.GetManagerTeamRequest{TeamId: 0})
	wantCode(t, err, codes.InvalidArgument)

	_, err = svc.GetManagerTeam(ctx, &pb.GetManagerTeamRequest{TeamId: 999})
	wantCode(t, err, codes.NotFound)
}

func TestGetManagerTeam_APIDown(t *testing.T) {
	svc, api := newTestService(t)
	api.SetDown(true)

	_, err := svc.GetManagerTeam(context.Background(), &pb.GetManagerTeamRequest{TeamId: 6})
	wantCode(t, err, codes.Unavailable)
}

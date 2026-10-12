package handlers

import (
	"context"
	"strings"
	"testing"

	"google.golang.org/grpc/codes"

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
)

// Players used below. Team 6 owns the first four; it already has 3 Arsenal
// and 3 Chelsea players, £0.1m in the bank and 2 free transfers.
const (
	gross        = 124 // MID, Brighton, sells for £5.6m, expected 11.2 points
	wissa        = 464 // FWD, sells for £6.1m
	muharemovic  = 334 // DEF, sells for £5.0m
	raya         = 1   // GK
	belloumi     = 286 // MID, Hull, £5.1m, expected 7.0
	kostoulas    = 138 // FWD, Brighton, £5.6m
	bogle        = 330 // DEF, Leeds, £4.6m
	saka         = 12  // MID, Arsenal, £9.5m
	unknownID    = 99999
	teamWithData = 6
)

// evaluate calls EvaluateTransfers for team 6 and fails the test on any error.
func evaluate(t *testing.T, svc *FplService, req *pb.EvaluateTransfersRequest) *pb.EvaluateTransfersResponse {
	t.Helper()
	req.TeamId = teamWithData
	resp, err := svc.EvaluateTransfers(context.Background(), req)
	if err != nil {
		t.Fatalf("EvaluateTransfers: %v", err)
	}
	return resp
}

// hasViolation reports whether any violation contains text.
func hasViolation(resp *pb.EvaluateTransfersResponse, text string) bool {
	for _, v := range resp.GetViolations() {
		if strings.Contains(v, text) {
			return true
		}
	}
	return false
}

func int32Ref(v int32) *int32 { return &v }

func TestEvaluateTransfers_Valid(t *testing.T) {
	svc, _ := newTestService(t)

	// Groß out (sells for £5.6m), Belloumi in (£5.1m): £0.1m + 5.6 - 5.1 = £0.6m left.
	resp := evaluate(t, svc, &pb.EvaluateTransfersRequest{PlayerOutIds: []int32{gross}, PlayerInIds: []int32{belloumi}})

	if !resp.GetValid() || len(resp.GetViolations()) != 0 {
		t.Fatalf("want valid, got violations %v", resp.GetViolations())
	}
	if resp.GetTransfers() != 1 || resp.GetHit() != 0 || resp.GetBankBefore() != 0.1 || resp.GetBankAfter() != 0.6 {
		t.Errorf("want 1 free transfer leaving £0.6m, got %d transfers, hit %d, bank %v -> %v",
			resp.GetTransfers(), resp.GetHit(), resp.GetBankBefore(), resp.GetBankAfter())
	}
	// 7.0 expected in, 11.2 out: a bad swap on next week's numbers.
	if resp.GetExpectedPointsIn() != 7.0 || resp.GetExpectedPointsOut() != 11.2 || resp.GetExpectedPointsNet() != -4.2 {
		t.Errorf("expected points: want 7.0 in, 11.2 out, -4.2 net; got %v, %v, %v",
			resp.GetExpectedPointsIn(), resp.GetExpectedPointsOut(), resp.GetExpectedPointsNet())
	}
	if len(resp.GetPlayersIn()) != 1 || len(resp.GetPlayersIn()[0].GetNextFixtures()) != outlookFixtures {
		t.Errorf("want Belloumi with his next fixtures, got %v", resp.GetPlayersIn())
	}
}

// Buying Saka for Groß breaks two rules at once: the money and the club limit.
func TestEvaluateTransfers_SakaBreaksRules(t *testing.T) {
	svc, _ := newTestService(t)

	resp := evaluate(t, svc, &pb.EvaluateTransfersRequest{PlayerOutIds: []int32{gross}, PlayerInIds: []int32{saka}})

	if resp.GetValid() {
		t.Fatal("want invalid")
	}
	// £0.1m + 5.6 - 9.5 = -£3.8m.
	if !hasViolation(resp, "£3.8m more than the budget") || resp.GetBankAfter() != -3.8 {
		t.Errorf("want a £3.8m budget violation, got %v (bank after %v)", resp.GetViolations(), resp.GetBankAfter())
	}
	if !hasViolation(resp, "4 Arsenal players") {
		t.Errorf("want a club limit violation, got %v", resp.GetViolations())
	}
}

func TestEvaluateTransfers_Hits(t *testing.T) {
	svc, _ := newTestService(t)
	// Three transfers with 2 free: one 4-point hit. Affordable:
	// £0.1m + (5.6 + 6.1 + 5.0) - (5.1 + 5.6 + 4.6) = £1.5m left.
	threeTransfers := func(maxHit *int32) *pb.EvaluateTransfersRequest {
		return &pb.EvaluateTransfersRequest{
			PlayerOutIds: []int32{gross, wissa, muharemovic},
			PlayerInIds:  []int32{belloumi, kostoulas, bogle},
			MaxHit:       maxHit,
		}
	}

	// No limit given: valid, with the hit reported.
	resp := evaluate(t, svc, threeTransfers(nil))
	if !resp.GetValid() || resp.GetHit() != 4 || resp.GetBankAfter() != 1.5 {
		t.Errorf("no limit: want valid with a 4-point hit and £1.5m left, got valid=%v hit=%d bank=%v %v",
			resp.GetValid(), resp.GetHit(), resp.GetBankAfter(), resp.GetViolations())
	}
	// The manager accepts a 4-point hit: still valid.
	if resp := evaluate(t, svc, threeTransfers(int32Ref(4))); !resp.GetValid() {
		t.Errorf("max_hit 4: want valid, got %v", resp.GetViolations())
	}
	// Free transfers only: invalid.
	if resp := evaluate(t, svc, threeTransfers(int32Ref(0))); resp.GetValid() || !hasViolation(resp, "4-point hit") {
		t.Errorf("max_hit 0: want a hit violation, got valid=%v %v", resp.GetValid(), resp.GetViolations())
	}
}

func TestEvaluateTransfers_Chips(t *testing.T) {
	svc, _ := newTestService(t)
	req := func(chip string) *pb.EvaluateTransfersRequest {
		return &pb.EvaluateTransfersRequest{
			PlayerOutIds: []int32{gross, wissa, muharemovic},
			PlayerInIds:  []int32{belloumi, kostoulas, bogle},
			Chip:         chip,
		}
	}

	// Team 6 still has its Free Hit: every transfer is free.
	if resp := evaluate(t, svc, req("freehit")); !resp.GetValid() || resp.GetHit() != 0 {
		t.Errorf("freehit: want valid with no hit, got valid=%v hit=%d %v", resp.GetValid(), resp.GetHit(), resp.GetViolations())
	}
	// Its first-half Wildcard is used up.
	if resp := evaluate(t, svc, req("wildcard")); resp.GetValid() || !hasViolation(resp, "wildcard chip isn't available") {
		t.Errorf("wildcard: want a chip violation, got %v", resp.GetViolations())
	}
	// Bench Boost doesn't make transfers free.
	if resp := evaluate(t, svc, req("bboost")); !hasViolation(resp, "only the wildcard or freehit") {
		t.Errorf("bboost: want a chip violation, got %v", resp.GetViolations())
	}
}

func TestEvaluateTransfers_RuleViolations(t *testing.T) {
	svc, _ := newTestService(t)
	cases := []struct {
		name string
		out  []int32
		in   []int32
		want string
	}{
		{"goalkeeper for a midfielder", []int32{raya}, []int32{belloumi}, "1 goalkeepers"},
		{"selling someone he doesn't own", []int32{belloumi}, []int32{kostoulas}, "not in the squad"},
		{"buying someone he owns", []int32{gross}, []int32{wissa}, "already in the squad"},
		{"uneven lists", []int32{gross, wissa}, []int32{belloumi}, "2 players out but 1 in"},
	}
	for _, tc := range cases {
		resp := evaluate(t, svc, &pb.EvaluateTransfersRequest{PlayerOutIds: tc.out, PlayerInIds: tc.in})
		if resp.GetValid() || !hasViolation(resp, tc.want) {
			t.Errorf("%s: want a violation mentioning %q, got valid=%v %v", tc.name, tc.want, resp.GetValid(), resp.GetViolations())
		}
	}
}

func TestEvaluateTransfers_BadRequests(t *testing.T) {
	ctx := context.Background()
	svc, _ := newTestService(t)

	_, err := svc.EvaluateTransfers(ctx, &pb.EvaluateTransfersRequest{TeamId: teamWithData})
	wantCode(t, err, codes.InvalidArgument) // nothing to evaluate

	_, err = svc.EvaluateTransfers(ctx, &pb.EvaluateTransfersRequest{
		TeamId: teamWithData, PlayerOutIds: []int32{gross}, PlayerInIds: []int32{unknownID},
	})
	wantCode(t, err, codes.NotFound)

	_, err = svc.EvaluateTransfers(ctx, &pb.EvaluateTransfersRequest{
		TeamId: 999, PlayerOutIds: []int32{gross}, PlayerInIds: []int32{belloumi},
	})
	wantCode(t, err, codes.NotFound)
}

func TestGetTransferTrends(t *testing.T) {
	ctx := context.Background()
	svc, _ := newTestService(t)

	// Direction left unset means "in".
	resp, err := svc.GetTransferTrends(ctx, &pb.GetTransferTrendsRequest{})
	if err != nil {
		t.Fatalf("GetTransferTrends: %v", err)
	}
	players := resp.GetPlayers()
	if resp.GetGameweek() != 6 || len(players) != defaultLimit {
		t.Fatalf("want %d players for gameweek 6, got %d for gameweek %d", defaultLimit, len(players), resp.GetGameweek())
	}
	top := players[0]
	if top.GetPlayer().GetWebName() != "Groß" || top.GetPlayer().GetTransfersInEvent() != 763845 || len(top.GetNextFixtures()) != outlookFixtures {
		t.Errorf("want Groß first with 763845 transfers in and his fixtures, got %+v", top)
	}

	out, err := svc.GetTransferTrends(ctx, &pb.GetTransferTrendsRequest{Direction: pb.TransferDirection_TRANSFER_DIRECTION_OUT, Limit: 3})
	if err != nil {
		t.Fatalf("GetTransferTrends(out): %v", err)
	}
	p := out.GetPlayers()
	if len(p) != 3 || p[0].GetPlayer().GetTransfersOutEvent() < p[1].GetPlayer().GetTransfersOutEvent() {
		t.Errorf("want 3 players sorted by transfers out, got %v", p)
	}
}

// The optimizer isn't written yet; the RPC must say so clearly.
func TestSuggestTransfers_NotImplemented(t *testing.T) {
	svc, _ := newTestService(t)

	_, err := svc.SuggestTransfers(context.Background(), &pb.SuggestTransfersRequest{TeamId: teamWithData})
	wantCode(t, err, codes.Unimplemented)
}

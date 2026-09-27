package handlers

import (
	"context"
	"testing"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
)

func TestSearchPlayers_EmptyNameIsInvalid(t *testing.T) {
	svc := NewFplService(nil, nil)

	_, err := svc.SearchPlayers(context.Background(), &pb.SearchPlayersRequest{Name: "  "})

	if got := status.Code(err); got != codes.InvalidArgument {
		t.Fatalf("want InvalidArgument, got %v (err=%v)", got, err)
	}
}

func TestSearchPlayers_ReturnsStub(t *testing.T) {
	svc := NewFplService(nil, nil)

	resp, err := svc.SearchPlayers(context.Background(), &pb.SearchPlayersRequest{Name: "saka"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(resp.GetPlayers()) == 0 {
		t.Fatal("expected at least one player")
	}
}

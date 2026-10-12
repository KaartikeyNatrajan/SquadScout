package fpl

import (
	"context"
	"errors"
	"testing"

	"squad_scout/fpl_data_service/internal/fpltest"
)

// Saved data: team 1 played a Free Hit in gameweek 5; team 6 made two normal
// transfers that week.

func TestFetchEntry(t *testing.T) {
	srv := fpltest.NewServer(t)

	e, err := NewClient(srv.URL).FetchEntry(context.Background(), 6)
	if err != nil {
		t.Fatalf("FetchEntry: %v", err)
	}
	if e.ID != 6 || e.StartedEvent != 1 || e.CurrentEvent == nil || *e.CurrentEvent != 5 {
		t.Errorf("entry 6: got %+v", e)
	}
}

func TestFetchEntry_UnknownTeam(t *testing.T) {
	srv := fpltest.NewServer(t)

	_, err := NewClient(srv.URL).FetchEntry(context.Background(), 999)
	// errors.Is looks through the wrapping (%w) for ErrNotFound.
	if !errors.Is(err, ErrNotFound) {
		t.Errorf("want ErrNotFound, got %v", err)
	}
}

func TestFetchEntryHistory(t *testing.T) {
	srv := fpltest.NewServer(t)

	h, err := NewClient(srv.URL).FetchEntryHistory(context.Background(), 6)
	if err != nil {
		t.Fatalf("FetchEntryHistory: %v", err)
	}
	if len(h.Current) != 5 || len(h.Chips) != 3 {
		t.Fatalf("want 5 gameweeks and 3 chips, got %d and %d", len(h.Current), len(h.Chips))
	}
	if gw5 := h.Current[4]; gw5.Event != 5 || gw5.EventTransfers != 2 || gw5.Bank != 1 {
		t.Errorf("gameweek 5: want 2 transfers and £0.1m in the bank, got %+v", gw5)
	}
}

func TestFetchEntryPicks(t *testing.T) {
	srv := fpltest.NewServer(t)
	client := NewClient(srv.URL)

	p, err := client.FetchEntryPicks(context.Background(), 1, 5)
	if err != nil {
		t.Fatalf("FetchEntryPicks: %v", err)
	}
	if len(p.Picks) != 15 || p.ActiveChip == nil || *p.ActiveChip != "freehit" {
		t.Errorf("team 1 gameweek 5: want 15 picks with the Free Hit active, got %d picks, chip %v", len(p.Picks), p.ActiveChip)
	}

	// A gameweek with no saved picks stands in for one whose deadline hasn't passed.
	if _, err := client.FetchEntryPicks(context.Background(), 1, 6); !errors.Is(err, ErrNotFound) {
		t.Errorf("unpublished gameweek: want ErrNotFound, got %v", err)
	}
}

func TestFetchEntryTransfers(t *testing.T) {
	srv := fpltest.NewServer(t)

	transfers, err := NewClient(srv.URL).FetchEntryTransfers(context.Background(), 6)
	if err != nil {
		t.Fatalf("FetchEntryTransfers: %v", err)
	}
	if len(transfers) != 21 {
		t.Errorf("want 21 transfers, got %d", len(transfers))
	}
	for _, tr := range transfers {
		if tr.ElementIn == 0 || tr.ElementInCost == 0 || tr.Event == 0 {
			t.Errorf("transfer with missing fields: %+v", tr)
		}
	}
}

func TestBootstrap_ChipsAndTransferFields(t *testing.T) {
	srv := fpltest.NewServer(t)

	b, err := NewClient(srv.URL).FetchBootstrap(context.Background())
	if err != nil {
		t.Fatalf("FetchBootstrap: %v", err)
	}
	// Two of each of the 4 chips: one per half of the season.
	if len(b.Chips) != 8 {
		t.Errorf("want 8 chip allowances, got %d", len(b.Chips))
	}
	for _, p := range b.Elements {
		if p.ID == 12 && (p.TransfersInEvent != 99910 || p.TransfersOutEvent != 44545) {
			t.Errorf("Saka transfers this gameweek: want 99910 in, 44545 out; got %d, %d", p.TransfersInEvent, p.TransfersOutEvent)
		}
	}
}

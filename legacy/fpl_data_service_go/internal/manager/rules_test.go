package manager

import (
	"slices"
	"testing"

	"squad_scout/fpl_data_service/internal/db"
	"squad_scout/fpl_data_service/internal/fpl"
)

// gw builds one gameweek of history.
func gw(event, transfers, cost int) fpl.EntryGameweek {
	return fpl.EntryGameweek{Event: event, EventTransfers: transfers, EventTransfersCost: cost}
}

func TestFreeTransfers(t *testing.T) {
	cases := []struct {
		name    string
		started int
		last    int
		history []fpl.EntryGameweek
		chips   []fpl.ChipPlay
		want    int
	}{
		{"just joined", 5, 5, nil, nil, 1},
		{"saved one", 1, 2, []fpl.EntryGameweek{gw(2, 0, 0)}, nil, 2},
		{"used one", 1, 2, []fpl.EntryGameweek{gw(2, 1, 0)}, nil, 1},
		{"capped at 5", 1, 10, nil, nil, 5},
		{"took a hit", 1, 3, []fpl.EntryGameweek{gw(2, 0, 0), gw(3, 3, 4)}, nil, 1},
		// A recorded hit corrects the estimate: whatever we thought, all the
		// free transfers were used.
		{"hit resyncs", 1, 4, []fpl.EntryGameweek{gw(4, 2, 4)}, nil, 1},
		// Team 6 in the saved data: nothing in GW2, Wildcard in GW3, 1
		// transfer in GW4, 2 in GW5: 1 -> 2 -> 3 -> 3 -> 2.
		{"team 6", 1, 5,
			[]fpl.EntryGameweek{gw(2, 0, 0), gw(3, 0, 0), gw(4, 1, 0), gw(5, 2, 0)},
			[]fpl.ChipPlay{{Name: "bboost", Event: 1}, {Name: "wildcard", Event: 3}, {Name: "3xc", Event: 4}},
			2},
	}
	for _, tc := range cases {
		if got := FreeTransfers(tc.started, tc.last, tc.history, tc.chips); got != tc.want {
			t.Errorf("%s: want %d free transfers, got %d", tc.name, tc.want, got)
		}
	}
}

func TestChipsAvailable(t *testing.T) {
	// The real rules: each chip once in gameweeks 1/2-19 and once in 20-38.
	rules := []db.ChipRow{
		{Name: "wildcard", StartEvent: 2, StopEvent: 19},
		{Name: "wildcard", StartEvent: 20, StopEvent: 38},
		{Name: "freehit", StartEvent: 2, StopEvent: 19},
		{Name: "bboost", StartEvent: 1, StopEvent: 19},
		{Name: "3xc", StartEvent: 1, StopEvent: 19},
		{Name: "freehit", StartEvent: 20, StopEvent: 38},
		{Name: "bboost", StartEvent: 20, StopEvent: 38},
		{Name: "3xc", StartEvent: 20, StopEvent: 38},
	}
	played := []fpl.ChipPlay{{Name: "bboost", Event: 1}, {Name: "wildcard", Event: 3}, {Name: "3xc", Event: 4}}

	// Gameweek 6: only the first-half Free Hit is left.
	if got := ChipsAvailable(rules, played, 6); !slices.Equal(got, []string{"freehit"}) {
		t.Errorf("gameweek 6: want [freehit], got %v", got)
	}
	// Gameweek 20: the second half brings a fresh set of all four.
	if got := ChipsAvailable(rules, played, 20); len(got) != 4 {
		t.Errorf("gameweek 20: want all 4 chips, got %v", got)
	}
}

func TestPurchasePrices(t *testing.T) {
	transfers := []fpl.EntryTransfer{
		{ElementIn: 10, ElementInCost: 50, Event: 3},
		{ElementIn: 10, ElementInCost: 55, Event: 7}, // bought again later: this one counts
		{ElementIn: 20, ElementInCost: 80, Event: 5}, // a Free Hit purchase: ignored
	}
	got := PurchasePrices(transfers, map[int]bool{5: true})

	if got[10] != 55 {
		t.Errorf("player 10: want the latest price 55, got %d", got[10])
	}
	if _, ok := got[20]; ok {
		t.Errorf("player 20 was only bought on a Free Hit; want no entry, got %d", got[20])
	}
}

func TestSellingPrice(t *testing.T) {
	cases := []struct{ purchase, now, want int }{
		{50, 50, 50}, // unchanged
		{50, 48, 48}, // fell: sells at the current price
		{50, 52, 51}, // rose 0.2: keeps half
		{50, 53, 51}, // rose 0.3: half is 0.15, rounded down to 0.1
		{155, 156, 155},
	}
	for _, tc := range cases {
		if got := SellingPrice(tc.purchase, tc.now); got != tc.want {
			t.Errorf("SellingPrice(%d, %d): want %d, got %d", tc.purchase, tc.now, tc.want, got)
		}
	}
}

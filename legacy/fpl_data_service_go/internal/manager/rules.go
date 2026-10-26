package manager

// FPL rules that the API doesn't apply for us. Each function works only on
// values passed in (no network, no database), so each is easy to test.

import (
	"squad_scout/fpl_data_service/internal/db"
	"squad_scout/fpl_data_service/internal/fpl"
	"squad_scout/fpl_data_service/internal/squad"
)

// FreeTransfers estimates how many free transfers a team has for the
// gameweek after lastEvent. FPL doesn't publish this, so it's rebuilt from
// the team's history with the 2024/25-onwards rules:
//
//   - After the team's first deadline it has 1 free transfer.
//   - Each gameweek, unused free transfers carry over, plus 1 more, up to
//     a maximum of 5.
//   - Transfers made for a gameweek use up its free transfers first; extra
//     ones cost points (a "hit") and leave 0 to carry over.
//   - Playing a Wildcard or Free Hit keeps the saved free transfers.
//
// ASSUMPTION: in a Wildcard or Free Hit gameweek the usual +1 is still
// added. The history can't confirm this (it shows 0 transfers either way);
// if it turns out wrong, change the chip case below.
func FreeTransfers(startedEvent, lastEvent int, history []fpl.EntryGameweek, chips []fpl.ChipPlay) int {
	transfersIn := map[int]fpl.EntryGameweek{}
	for _, h := range history {
		transfersIn[h.Event] = h
	}
	chipIn := map[int]string{}
	for _, c := range chips {
		chipIn[c.Event] = c.Name
	}

	free := 1 // available for the gameweek after the first one
	for gw := startedEvent + 1; gw <= lastEvent; gw++ {
		switch chipIn[gw] {
		case squad.ChipWildcard, squad.ChipFreeHit:
			// Saved free transfers are kept; nothing is used up.
		default:
			h := transfersIn[gw]
			free -= h.EventTransfers
			// A hit means more transfers were made than were free, so none
			// carry over. Trusting the recorded cost also corrects the
			// estimate if it had drifted.
			if free < 0 || h.EventTransfersCost > 0 {
				free = 0
			}
		}
		free = min(free+1, squad.MaxFreeTransfers)
	}
	return free
}

// ChipsAvailable returns the chips a team can still play in gameweek gw,
// e.g. ["freehit", "3xc"]. Each allowance in rules may be played once
// within its window; a play inside a window uses that window's allowance.
func ChipsAvailable(rules []db.ChipRow, played []fpl.ChipPlay, gw int) []string {
	var out []string
	listed := map[string]bool{} // in case two windows of a chip overlap gw
	for _, r := range rules {
		if gw < r.StartEvent || gw > r.StopEvent || listed[r.Name] {
			continue
		}
		used := false
		for _, p := range played {
			if p.Name == r.Name && p.Event >= r.StartEvent && p.Event <= r.StopEvent {
				used = true
				break
			}
		}
		if !used {
			out = append(out, r.Name)
			listed[r.Name] = true
		}
	}
	return out
}

// PurchasePrices returns what the team paid for each player it bought,
// keyed by player id, using his most recent purchase. Free Hit gameweeks are
// skipped: their transfers are undone when the squad returns to normal.
// Players in the squad since the team was created have no entry, because
// the API doesn't record their price.
func PurchasePrices(transfers []fpl.EntryTransfer, freeHitEvents map[int]bool) map[int]int {
	prices := map[int]int{}
	latest := map[int]int{} // player id -> gameweek of the purchase in prices
	for _, t := range transfers {
		if freeHitEvents[t.Event] {
			continue
		}
		if t.Event >= latest[t.ElementIn] {
			latest[t.ElementIn] = t.Event
			prices[t.ElementIn] = t.ElementInCost
		}
	}
	return prices
}

// SellingPrice is what FPL pays when a player is sold: his current price if
// it hasn't risen since he was bought, otherwise his purchase price plus
// half of the rise, rounded down to the nearest £0.1m. All in tenths of £m.
func SellingPrice(purchase, now int) int {
	if now <= purchase {
		return now
	}
	return purchase + (now-purchase)/2 // integer division rounds down
}

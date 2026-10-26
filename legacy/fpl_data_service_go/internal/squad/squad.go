// Package squad holds FPL's squad rules and checks squads against them: the
// "verifier". Given a squad and a set of transfers, it works out the squad
// that results and lists every rule it breaks, in plain words.
//
// It only does arithmetic on the values it's given: no database, no network.
// That keeps it easy to test, and fast enough for the optimizer to check
// thousands of candidate transfer sets.
package squad

import (
	"fmt"
	"sort"
)

// FPL's squad rules (they're also in /bootstrap-static/ "game_settings").
const (
	Size             = 15 // players in a squad
	MaxPerClub       = 3  // players from any one Premier League club
	HitPerTransfer   = 4  // points deducted per transfer beyond the free ones
	MaxFreeTransfers = 5  // free transfers can be saved up to this many
)

// Quota is how many players of each position a squad must have, keyed by
// FPL's position number (1 GK, 2 DEF, 3 MID, 4 FWD).
var Quota = map[int]int{1: 2, 2: 5, 3: 5, 4: 3}

var positionNames = map[int]string{1: "goalkeepers", 2: "defenders", 3: "midfielders", 4: "forwards"}

// Member is one player, with just what the rules need.
type Member struct {
	PlayerID int
	Name     string // only used in messages
	Position int    // 1 GK .. 4 FWD
	ClubID   int
	ClubName string // only used in messages
	// Tenths of £m: what he'd sell for if he's in the squad, or what he
	// costs to buy if he's coming in.
	Price int
}

// Squad is a set of players plus the money left over.
type Squad struct {
	Members []Member
	Bank    int // tenths of £m; negative means over budget
}

// Validate lists every rule s breaks. An empty list means s is a legal squad.
func Validate(s Squad) []string {
	var problems []string

	if len(s.Members) != Size {
		problems = append(problems, fmt.Sprintf("the squad has %d players; it must have exactly %d", len(s.Members), Size))
	}

	// Count players per position and per club in one pass.
	perPosition := map[int]int{}
	perClub := map[int]int{}
	clubNames := map[int]string{}
	seen := map[int]bool{}
	for _, m := range s.Members {
		if seen[m.PlayerID] {
			problems = append(problems, fmt.Sprintf("%s is in the squad twice", m.Name))
		}
		seen[m.PlayerID] = true
		perPosition[m.Position]++
		perClub[m.ClubID]++
		clubNames[m.ClubID] = m.ClubName
	}

	// Positions in a fixed order (GK, DEF, MID, FWD) so messages are stable.
	for pos := 1; pos <= 4; pos++ {
		if got, want := perPosition[pos], Quota[pos]; got != want {
			problems = append(problems, fmt.Sprintf("the squad would have %d %s; it must have exactly %d", got, positionNames[pos], want))
		}
	}

	// Map iteration order is random in Go, so sort the club ids first.
	clubIDs := make([]int, 0, len(perClub))
	for id := range perClub {
		clubIDs = append(clubIDs, id)
	}
	sort.Ints(clubIDs)
	for _, id := range clubIDs {
		if perClub[id] > MaxPerClub {
			problems = append(problems, fmt.Sprintf("the squad would have %d %s players; the limit is %d", perClub[id], clubNames[id], MaxPerClub))
		}
	}

	if s.Bank < 0 {
		problems = append(problems, fmt.Sprintf("the squad costs %s more than the budget allows", money(-s.Bank)))
	}
	return problems
}

// Apply sells the players in outIDs, buys the players in in, and returns
// the resulting squad with every problem found: transfers that make no sense
// (selling someone not in the squad, buying someone already in it) and every
// rule the resulting squad breaks.
//
// If the transfers themselves make no sense, the original squad is returned
// unchanged with those problems; checking the rules on top would only add noise.
func Apply(s Squad, outIDs []int, in []Member) (Squad, []string) {
	var problems []string
	if len(outIDs) != len(in) {
		problems = append(problems, fmt.Sprintf("%d players out but %d in; the numbers must match", len(outIDs), len(in)))
	}

	// Index the squad by player id for quick lookups.
	current := make(map[int]Member, len(s.Members))
	for _, m := range s.Members {
		current[m.PlayerID] = m
	}

	leaving := map[int]bool{}
	for _, id := range outIDs {
		m, ok := current[id]
		switch {
		case !ok:
			problems = append(problems, fmt.Sprintf("player %d is not in the squad, so he can't be sold", id))
		case leaving[id]:
			problems = append(problems, fmt.Sprintf("%s is sold twice", m.Name))
		}
		leaving[id] = true
	}

	joining := map[int]bool{}
	for _, m := range in {
		switch {
		case leaving[m.PlayerID]:
			problems = append(problems, fmt.Sprintf("%s is both sold and bought", m.Name))
		case current[m.PlayerID].PlayerID != 0: // the zero Member means "not found"
			problems = append(problems, fmt.Sprintf("%s is already in the squad", m.Name))
		case joining[m.PlayerID]:
			problems = append(problems, fmt.Sprintf("%s is bought twice", m.Name))
		}
		joining[m.PlayerID] = true
	}

	if len(problems) > 0 {
		return s, problems
	}

	// Build the new squad: everyone not leaving, plus the new players.
	// Selling adds each player's selling price to the bank; buying subtracts
	// the new player's price.
	next := Squad{Bank: s.Bank}
	for _, m := range s.Members {
		if leaving[m.PlayerID] {
			next.Bank += m.Price
			continue
		}
		next.Members = append(next.Members, m)
	}
	for _, m := range in {
		next.Bank -= m.Price
		next.Members = append(next.Members, m)
	}
	return next, Validate(next)
}

// Chips that make every transfer in their gameweek free.
const (
	ChipWildcard = "wildcard"
	ChipFreeHit  = "freehit"
)

// HitCost returns the points deducted for making transfers when
// freeTransfers are available. Playing a Wildcard or Free Hit (chip) makes
// them all free.
func HitCost(transfers, freeTransfers int, chip string) int {
	if chip == ChipWildcard || chip == ChipFreeHit {
		return 0
	}
	extra := transfers - freeTransfers
	if extra <= 0 {
		return 0
	}
	return extra * HitPerTransfer
}

// money formats tenths of £m as "£1.5m".
func money(tenths int) string {
	return fmt.Sprintf("£%.1fm", float64(tenths)/10)
}

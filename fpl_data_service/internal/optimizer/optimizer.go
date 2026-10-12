// Package optimizer will search for the best transfers for a squad.
//
// NOT IMPLEMENTED YET. The types and wiring are in place (the SuggestTransfers
// RPC builds a Request and calls Suggest); only the search itself is missing.
//
// Intended design:
//
//  1. Candidates: for each player in the squad, the best few replacements in
//     the same position that are affordable (the handler gathers these with
//     db.FindPlayers, so the search space stays small).
//  2. Search: try every combination of up to MaxTransfers swaps. With ~10
//     candidates per position that's small enough to enumerate; if it ever
//     isn't, switch to an integer-programming solver.
//  3. Verify: run each combination through squad.Apply, the same verifier
//     EvaluateTransfers uses, and drop any that break a rule. The optimizer
//     never needs its own copy of the rules.
//  4. Score: expected points gained minus squad.HitCost, dropping any whose
//     hit exceeds MaxHit. Return the best few, best first.
//
// Keep it free of I/O like the squad package: everything it needs arrives in
// Request, which makes it easy to test with made-up squads.
package optimizer

import (
	"context"
	"errors"

	"squad_scout/fpl_data_service/internal/squad"
)

// ErrNotImplemented is returned by Suggest until the search is written.
var ErrNotImplemented = errors.New("the transfer optimizer is not implemented yet")

// Request is everything the search needs. Money is in tenths of £m.
type Request struct {
	Squad         squad.Squad // the current squad, with selling prices and bank
	FreeTransfers int
	MaxTransfers  int  // the most transfers to consider
	MaxHit        *int // nil = no limit on points deducted
	// ExpectedPoints of each current squad member, keyed by player id.
	ExpectedPoints map[int]float64
	// Candidates are the players that may be brought in.
	Candidates []Candidate
}

// Candidate is a player who could be transferred in.
type Candidate struct {
	Member         squad.Member // Price = what he costs to buy
	ExpectedPoints float64
}

// Suggestion is one legal set of transfers.
type Suggestion struct {
	OutIDs       []int
	InIDs        []int
	Hit          int
	ExpectedGain float64 // expected points gained, after the hit
}

// Suggest returns the best legal transfer sets for req, best first.
func Suggest(ctx context.Context, req Request) ([]Suggestion, error) {
	return nil, ErrNotImplemented
}

package fpl

// A manager's team. FPL calls a team an "entry". Its id is the number in the
// URL of the manager's points page. All of these endpoints are public.
//
//	/entry/{id}/                    the team's name, manager, overall points
//	/entry/{id}/history/            points, bank and transfers per gameweek; chips played
//	/entry/{id}/event/{gw}/picks/   the 15 players picked for one gameweek
//	/entry/{id}/transfers/          every transfer made, with prices
//
// Picks are only published once a gameweek's deadline has passed, so the
// newest squad anyone can see is the last deadline's.

import (
	"context"
	"fmt"
)

// Entry is a partial view of /entry/{id}/.
type Entry struct {
	ID              int    `json:"id"`
	Name            string `json:"name"` // the team's name
	PlayerFirstName string `json:"player_first_name"`
	PlayerLastName  string `json:"player_last_name"`
	StartedEvent    int    `json:"started_event"` // first gameweek the team played
	// The last gameweek whose deadline has passed; null before the team's
	// first deadline.
	CurrentEvent         *int `json:"current_event"`
	SummaryOverallPoints int  `json:"summary_overall_points"`
	SummaryOverallRank   *int `json:"summary_overall_rank"` // null before the first gameweek is scored
}

// EntryHistory is a partial view of /entry/{id}/history/.
type EntryHistory struct {
	Current []EntryGameweek `json:"current"` // one per gameweek played this season
	Chips   []ChipPlay      `json:"chips"`
}

// EntryGameweek is one team's numbers for one gameweek.
type EntryGameweek struct {
	Event       int `json:"event"`
	Points      int `json:"points"`
	TotalPoints int `json:"total_points"`
	Bank        int `json:"bank"`  // tenths of £m, after that gameweek's transfers
	Value       int `json:"value"` // squad value, tenths of £m
	// Transfers made for this gameweek and the points they cost. Transfers
	// made with a Wildcard or Free Hit aren't counted here.
	EventTransfers     int `json:"event_transfers"`
	EventTransfersCost int `json:"event_transfers_cost"`
}

// ChipPlay is one chip a team has played.
type ChipPlay struct {
	Name  string `json:"name"`
	Event int    `json:"event"`
}

// EntryPicks is /entry/{id}/event/{gw}/picks/.
type EntryPicks struct {
	ActiveChip   *string       `json:"active_chip"` // null if no chip was played
	EntryHistory EntryGameweek `json:"entry_history"`
	Picks        []Pick        `json:"picks"`
}

// Pick is one of the 15 players in a squad.
type Pick struct {
	Element       int  `json:"element"`  // player id
	Position      int  `json:"position"` // 1-11 starting XI, 12-15 bench
	Multiplier    int  `json:"multiplier"`
	IsCaptain     bool `json:"is_captain"`
	IsViceCaptain bool `json:"is_vice_captain"`
}

// EntryTransfer is one transfer from /entry/{id}/transfers/.
type EntryTransfer struct {
	ElementIn      int `json:"element_in"`
	ElementInCost  int `json:"element_in_cost"` // what he was bought for, tenths of £m
	ElementOut     int `json:"element_out"`
	ElementOutCost int `json:"element_out_cost"`
	Event          int `json:"event"` // the gameweek the transfer was made for
}

// FetchEntry downloads a team's summary. Unknown ids give ErrNotFound.
func (c *Client) FetchEntry(ctx context.Context, teamID int) (*Entry, error) {
	var out Entry
	if err := c.getJSON(ctx, fmt.Sprintf("/entry/%d/", teamID), &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// FetchEntryHistory downloads a team's gameweek-by-gameweek history and chips.
func (c *Client) FetchEntryHistory(ctx context.Context, teamID int) (*EntryHistory, error) {
	var out EntryHistory
	if err := c.getJSON(ctx, fmt.Sprintf("/entry/%d/history/", teamID), &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// FetchEntryPicks downloads a team's squad for one gameweek. Gameweeks whose
// deadline hasn't passed give ErrNotFound.
func (c *Client) FetchEntryPicks(ctx context.Context, teamID, gameweek int) (*EntryPicks, error) {
	var out EntryPicks
	if err := c.getJSON(ctx, fmt.Sprintf("/entry/%d/event/%d/picks/", teamID, gameweek), &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// FetchEntryTransfers downloads every transfer a team has made this season.
func (c *Client) FetchEntryTransfers(ctx context.Context, teamID int) ([]EntryTransfer, error) {
	var out []EntryTransfer
	if err := c.getJSON(ctx, fmt.Sprintf("/entry/%d/transfers/", teamID), &out); err != nil {
		return nil, err
	}
	return out, nil
}

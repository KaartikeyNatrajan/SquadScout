package fpl

import (
	"context"
	"fmt"
)

// /event/{gw}/live/ has every player's stats for one gameweek in one
// response. It's how we build players' match histories: one download per
// gameweek covers all ~700 players, instead of one download per player.

// EventLive is /event/{gw}/live/.
type EventLive struct {
	Elements []LiveElement `json:"elements"`
}

// LiveElement is one player's gameweek.
type LiveElement struct {
	ID    int       `json:"id"`    // player id
	Stats LiveStats `json:"stats"` // totals for the whole gameweek
	// One entry per match his team played that gameweek: usually one, two in
	// a double gameweek, none in a blank one. Present even if he didn't play.
	Explain []LiveExplain `json:"explain"`
}

// LiveStats is a player's totals for one gameweek (all of his matches in it).
type LiveStats struct {
	Minutes                  int    `json:"minutes"`
	Starts                   int    `json:"starts"`
	GoalsScored              int    `json:"goals_scored"`
	Assists                  int    `json:"assists"`
	CleanSheets              int    `json:"clean_sheets"`
	GoalsConceded            int    `json:"goals_conceded"`
	Saves                    int    `json:"saves"`
	Bonus                    int    `json:"bonus"`
	YellowCards              int    `json:"yellow_cards"`
	RedCards                 int    `json:"red_cards"`
	TotalPoints              int    `json:"total_points"`
	ExpectedGoals            string `json:"expected_goals"` // a string, like in bootstrap
	ExpectedAssists          string `json:"expected_assists"`
	ExpectedGoalInvolvements string `json:"expected_goal_involvements"`
}

// LiveExplain is how a player scored his points in one match.
type LiveExplain struct {
	Fixture int               `json:"fixture"`
	Stats   []LiveExplainStat `json:"stats"`
}

// LiveExplainStat is one scoring line, e.g. {minutes, value 80, points 2}.
type LiveExplainStat struct {
	Identifier         string `json:"identifier"` // "minutes", "goals_scored", "bonus", ...
	Value              int    `json:"value"`
	Points             int    `json:"points"`
	PointsModification int    `json:"points_modification"`
}

// Minutes returns the minutes he played in this match.
func (e LiveExplain) Minutes() int {
	for _, s := range e.Stats {
		if s.Identifier == "minutes" {
			return s.Value
		}
	}
	return 0
}

// Points returns the FPL points he scored in this match: the sum of every
// scoring line. Across all of a gameweek's matches this adds up to
// Stats.TotalPoints.
func (e LiveExplain) Points() int {
	total := 0
	for _, s := range e.Stats {
		total += s.Points + s.PointsModification
	}
	return total
}

// FetchEventLive downloads every player's stats for one gameweek.
func (c *Client) FetchEventLive(ctx context.Context, gameweek int) (*EventLive, error) {
	var out EventLive
	if err := c.getJSON(ctx, fmt.Sprintf("/event/%d/live/", gameweek), &out); err != nil {
		return nil, err
	}
	return &out, nil
}

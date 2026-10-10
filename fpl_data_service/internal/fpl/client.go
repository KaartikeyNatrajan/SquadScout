// Package fpl is a thin client for the public Fantasy Premier League API.
//
// Endpoints we use (all GET, no auth):
//
//	/bootstrap-static/       all players ("elements"), teams, gameweeks ("events")
//	/fixtures/               every fixture of the season, with difficulty ratings
//	/element-summary/{id}/   one player's match-by-match history this season
//
// This package only downloads and decodes JSON. Deciding when to download
// (internal/refresh) and storing the result (internal/db) live elsewhere.
package fpl

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

// DefaultBaseURL is the real FPL API. Tests pass the URL of a fake server.
const DefaultBaseURL = "https://fantasy.premierleague.com/api"

// The FPL API sometimes returns 403 to requests without a browser-like UA.
const userAgent = "Mozilla/5.0 (compatible; squad-scout/0.1)"

// Client holds the base URL and a reusable HTTP client (reusing it keeps
// connections open between requests).
type Client struct {
	baseURL string
	http    *http.Client
}

// NewClient returns a client for the API at baseURL (normally DefaultBaseURL).
func NewClient(baseURL string) *Client {
	return &Client{
		baseURL: baseURL,
		http:    &http.Client{Timeout: 15 * time.Second},
	}
}

// The structs below mirror the JSON the FPL API sends. The `json:"..."` tags
// tell Go's JSON decoder which JSON key fills which field; keys we don't list
// are simply ignored.
//
// Two FPL quirks show up throughout:
//   - Many decimals arrive as strings ("6.2"), so those fields are strings
//     here and get converted when stored.
//   - Fields that can be JSON null are pointers: nil means null. A plain int
//     would turn null into 0, which often means something else (0% chance of
//     playing = definitely out, but null = no concerns).

// Bootstrap is a partial view of /bootstrap-static/. Add fields as you need them.
type Bootstrap struct {
	Elements []Element `json:"elements"`
	Teams    []Team    `json:"teams"`
	Events   []Event   `json:"events"`
}

// Element is FPL's name for a player.
type Element struct {
	ID                       int    `json:"id"`
	WebName                  string `json:"web_name"`
	FirstName                string `json:"first_name"`
	SecondName               string `json:"second_name"`
	Team                     int    `json:"team"`
	ElementType              int    `json:"element_type"` // 1 GK, 2 DEF, 3 MID, 4 FWD
	NowCost                  int    `json:"now_cost"`     // tenths of £m
	TotalPoints              int    `json:"total_points"`
	Form                     string `json:"form"`                // FPL sends numbers as strings here
	SelectedByPercent        string `json:"selected_by_percent"` // same
	Status                   string `json:"status"`              // a, d, i, s, u, n
	News                     string `json:"news"`                // "" when there is none
	ChanceOfPlayingNextRound *int   `json:"chance_of_playing_next_round"`
	Minutes                  int    `json:"minutes"`
	Starts                   int    `json:"starts"`
	GoalsScored              int    `json:"goals_scored"`
	Assists                  int    `json:"assists"`
	CleanSheets              int    `json:"clean_sheets"`
	Bonus                    int    `json:"bonus"`
	ExpectedGoals            string `json:"expected_goals"`
	ExpectedAssists          string `json:"expected_assists"`
	ExpectedGoalInvolvements string `json:"expected_goal_involvements"`
	PointsPerGame            string `json:"points_per_game"`
	EPNext                   string `json:"ep_next"` // FPL's expected points for the next gameweek
}

// Team is one Premier League club.
type Team struct {
	ID        int    `json:"id"`
	Name      string `json:"name"`
	ShortName string `json:"short_name"`
}

// Event is FPL's name for a gameweek.
type Event struct {
	ID         int    `json:"id"`
	Name       string `json:"name"`
	Deadline   string `json:"deadline_time"`
	IsCurrent  bool   `json:"is_current"`
	IsNext     bool   `json:"is_next"`
	IsFinished bool   `json:"finished"`
}

// Fixture is one match from /fixtures/. "h" is the home team, "a" the away team.
type Fixture struct {
	ID              int     `json:"id"`
	Event           *int    `json:"event"`        // gameweek; null if not scheduled yet
	KickoffTime     *string `json:"kickoff_time"` // RFC 3339; null if not scheduled yet
	TeamH           int     `json:"team_h"`
	TeamA           int     `json:"team_a"`
	TeamHDifficulty int     `json:"team_h_difficulty"` // 1 (easy) - 5 (hard)
	TeamADifficulty int     `json:"team_a_difficulty"`
	Finished        bool    `json:"finished"`
	TeamHScore      *int    `json:"team_h_score"` // null until the match starts
	TeamAScore      *int    `json:"team_a_score"`
}

// ElementSummary is a partial view of /element-summary/{id}/. It also has
// "fixtures" (upcoming) and "history_past" (previous seasons), but upcoming
// fixtures come from /fixtures/ instead, which covers every team at once.
type ElementSummary struct {
	History []HistoryEntry `json:"history"`
}

// HistoryEntry is one match a player's team played this season (from the
// moment he joined), including matches he didn't play in (Minutes == 0).
type HistoryEntry struct {
	Element                  int    `json:"element"` // the player's id
	Fixture                  int    `json:"fixture"`
	Round                    int    `json:"round"` // gameweek
	OpponentTeam             int    `json:"opponent_team"`
	WasHome                  bool   `json:"was_home"`
	KickoffTime              string `json:"kickoff_time"`
	TeamHScore               *int   `json:"team_h_score"`
	TeamAScore               *int   `json:"team_a_score"`
	TotalPoints              int    `json:"total_points"`
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
	ExpectedGoals            string `json:"expected_goals"`
	ExpectedAssists          string `json:"expected_assists"`
	ExpectedGoalInvolvements string `json:"expected_goal_involvements"`
}

// FetchBootstrap downloads /bootstrap-static/ (every player, team and gameweek
// in one big JSON document, about 3 MB).
func (c *Client) FetchBootstrap(ctx context.Context) (*Bootstrap, error) {
	var out Bootstrap
	if err := c.getJSON(ctx, "/bootstrap-static/", &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// FetchFixtures downloads every fixture of the season (380 matches).
func (c *Client) FetchFixtures(ctx context.Context) ([]Fixture, error) {
	var out []Fixture
	if err := c.getJSON(ctx, "/fixtures/", &out); err != nil {
		return nil, err
	}
	return out, nil
}

// FetchElementSummary downloads one player's match history this season.
func (c *Client) FetchElementSummary(ctx context.Context, playerID int) (*ElementSummary, error) {
	var out ElementSummary
	if err := c.getJSON(ctx, fmt.Sprintf("/element-summary/%d/", playerID), &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// getJSON does a GET request and decodes the JSON body into dst. dst must be
// a pointer (e.g. &out) so the decoder can fill in the caller's variable.
func (c *Client) getJSON(ctx context.Context, path string, dst any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+path, nil)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", userAgent)
	req.Header.Set("Accept", "application/json")

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("GET %s: %w", path, err)
	}
	defer resp.Body.Close() // always close the body, or the connection leaks

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("GET %s: unexpected status %d", path, resp.StatusCode)
	}
	if err := json.NewDecoder(resp.Body).Decode(dst); err != nil {
		return fmt.Errorf("GET %s: decode: %w", path, err)
	}
	return nil
}

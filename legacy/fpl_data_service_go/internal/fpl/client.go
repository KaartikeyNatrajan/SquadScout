// Package fpl is a thin client for the public Fantasy Premier League API.
//
// Endpoints we use (all GET, no auth):
//
//	/bootstrap-static/       all players ("elements"), teams, gameweeks ("events")
//	/fixtures/               every fixture of the season, with difficulty ratings
//	/event/{gw}/live/        every player's stats in one gameweek (see live.go)
//	/entry/{id}/...          one manager's team (see entry.go)
//
// This package only downloads and decodes JSON. Deciding when to download
// (internal/refresh) and storing the result (internal/db) live elsewhere.
package fpl

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"time"
)

// DefaultBaseURL is the real FPL API. Tests pass the URL of a fake server.
const DefaultBaseURL = "https://fantasy.premierleague.com/api"

// ErrNotFound is returned (wrapped) when the API answers 404, e.g. for an
// unknown player or team id. Check for it with errors.Is(err, fpl.ErrNotFound).
var ErrNotFound = errors.New("not found")

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
	Chips    []Chip    `json:"chips"`
}

// Element is FPL's name for a player.
type Element struct {
	ID                       int     `json:"id"`
	WebName                  string  `json:"web_name"`
	FirstName                string  `json:"first_name"`
	SecondName               string  `json:"second_name"`
	Team                     int     `json:"team"`
	ElementType              int     `json:"element_type"` // 1 GK, 2 DEF, 3 MID, 4 FWD
	NowCost                  int     `json:"now_cost"`     // tenths of £m
	TotalPoints              int     `json:"total_points"`
	Form                     string  `json:"form"`                // FPL sends numbers as strings here
	SelectedByPercent        string  `json:"selected_by_percent"` // same
	Status                   string  `json:"status"`              // a, d, i, s, u, n
	News                     string  `json:"news"`                // "" when there is none
	ChanceOfPlayingNextRound *int    `json:"chance_of_playing_next_round"`
	Minutes                  int     `json:"minutes"`
	Starts                   int     `json:"starts"`
	GoalsScored              int     `json:"goals_scored"`
	Assists                  int     `json:"assists"`
	CleanSheets              int     `json:"clean_sheets"`
	Bonus                    int     `json:"bonus"`
	ExpectedGoals            string  `json:"expected_goals"`
	ExpectedAssists          string  `json:"expected_assists"`
	ExpectedGoalInvolvements string  `json:"expected_goal_involvements"`
	PointsPerGame            string  `json:"points_per_game"`
	EPNext                   string  `json:"ep_next"`             // FPL's expected points for the next gameweek
	TransfersInEvent         int     `json:"transfers_in_event"`  // since the last deadline
	TransfersOutEvent        int     `json:"transfers_out_event"` // same
	CostChangeEvent          int     `json:"cost_change_event"`   // tenths of £m since the last deadline
	NewsAdded                *string `json:"news_added"`          // RFC 3339; null if no news
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

// Chip is one chip allowance: each manager may play the chip once between
// StartEvent and StopEvent. FPL gives two of each chip per season, one per
// half, so each name appears twice.
type Chip struct {
	ID         int    `json:"id"`
	Name       string `json:"name"` // "wildcard", "freehit", "bboost", "3xc"
	StartEvent int    `json:"start_event"`
	StopEvent  int    `json:"stop_event"`
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

	if resp.StatusCode == http.StatusNotFound {
		// %w wraps ErrNotFound, so callers can detect it with errors.Is.
		return fmt.Errorf("GET %s: %w", path, ErrNotFound)
	}
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("GET %s: unexpected status %d", path, resp.StatusCode)
	}
	if err := json.NewDecoder(resp.Body).Decode(dst); err != nil {
		return fmt.Errorf("GET %s: decode: %w", path, err)
	}
	return nil
}

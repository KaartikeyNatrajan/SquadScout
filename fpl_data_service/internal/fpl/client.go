// Package fpl is a thin client for the public Fantasy Premier League API.
//
// Useful endpoints (all GET, no auth):
//
//	/bootstrap-static/          all players ("elements"), teams, gameweeks ("events")
//	/fixtures/?event={gw}       fixtures, optionally filtered by gameweek
//	/element-summary/{id}/      one player's per-gameweek history + upcoming fixtures
//	/event/{gw}/live/           live stats for every player in a gameweek
package fpl

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

const defaultBaseURL = "https://fantasy.premierleague.com/api"

// The FPL API sometimes returns 403 to requests without a browser-like UA.
const userAgent = "Mozilla/5.0 (compatible; squad-scout/0.1)"

type Client struct {
	baseURL string
	http    *http.Client
}

func NewClient() *Client {
	return &Client{
		baseURL: defaultBaseURL,
		http:    &http.Client{Timeout: 15 * time.Second},
	}
}

// Bootstrap is a partial view of /bootstrap-static/. Add fields as you need them.
type Bootstrap struct {
	Elements []Element `json:"elements"`
	Teams    []Team    `json:"teams"`
	Events   []Event   `json:"events"`
}

type Element struct {
	ID                int    `json:"id"`
	WebName           string `json:"web_name"`
	FirstName         string `json:"first_name"`
	SecondName        string `json:"second_name"`
	Team              int    `json:"team"`
	ElementType       int    `json:"element_type"` // 1 GK, 2 DEF, 3 MID, 4 FWD
	NowCost           int    `json:"now_cost"`     // tenths of £m
	TotalPoints       int    `json:"total_points"`
	Form              string `json:"form"`                // FPL sends numbers as strings here
	SelectedByPercent string `json:"selected_by_percent"` // same
	Status            string `json:"status"`
}

type Team struct {
	ID        int    `json:"id"`
	Name      string `json:"name"`
	ShortName string `json:"short_name"`
}

type Event struct {
	ID         int    `json:"id"`
	Name       string `json:"name"`
	Deadline   string `json:"deadline_time"`
	IsCurrent  bool   `json:"is_current"`
	IsNext     bool   `json:"is_next"`
	IsFinished bool   `json:"finished"`
}

func (c *Client) FetchBootstrap(ctx context.Context) (*Bootstrap, error) {
	var out Bootstrap
	if err := c.getJSON(ctx, "/bootstrap-static/", &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// TODO: FetchFixtures(ctx, gw), FetchElementSummary(ctx, playerID)

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
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("GET %s: unexpected status %d", path, resp.StatusCode)
	}
	return json.NewDecoder(resp.Body).Decode(dst)
}

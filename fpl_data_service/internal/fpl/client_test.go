package fpl

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"squad_scout/fpl_data_service/internal/fpltest"
)

func TestReadingBootstrap_ReturnsBootstrap(t *testing.T) {
	var b Bootstrap
	if err := json.Unmarshal(fpltest.ReadFile(t, "bootstrap-static.json"), &b); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(b.Teams) != 20 {
		t.Errorf("teams: want 20, got %d", len(b.Teams))
	}
	if len(b.Elements) != 667 {
		t.Errorf("players: want 667, got %d", len(b.Elements))
	}
	if len(b.Events) != 38 {
		t.Errorf("gameweeks: want 38, got %d", len(b.Events))
	}
}

func TestFetchBootstrap(t *testing.T) {
	srv := fpltest.NewServer(t)

	b, err := NewClient(srv.URL).FetchBootstrap(context.Background())
	if err != nil {
		t.Fatalf("FetchBootstrap: %v", err)
	}
	if len(b.Elements) != 667 {
		t.Errorf("players: want 667, got %d", len(b.Elements))
	}
}

func TestFetchFixtures(t *testing.T) {
	srv := fpltest.NewServer(t)

	fixtures, err := NewClient(srv.URL).FetchFixtures(context.Background())
	if err != nil {
		t.Fatalf("FetchFixtures: %v", err)
	}
	if len(fixtures) != 380 {
		t.Fatalf("fixtures: want 380, got %d", len(fixtures))
	}

	// Fixture 1 was played: Arsenal (1) 3-0 Coventry (7).
	first := fixtures[0]
	if first.ID != 1 || !first.Finished || first.TeamH != 1 || first.TeamA != 7 {
		t.Errorf("fixture 1: got %+v", first)
	}
	if first.TeamHScore == nil || *first.TeamHScore != 3 {
		t.Errorf("fixture 1 home score: want 3, got %v", first.TeamHScore)
	}

	// An unplayed fixture must decode its null scores as nil, not 0.
	for _, f := range fixtures {
		if !f.Finished {
			if f.TeamHScore != nil || f.TeamAScore != nil {
				t.Errorf("unplayed fixture %d has scores %v-%v", f.ID, f.TeamHScore, f.TeamAScore)
			}
			break
		}
	}
}

func TestFetchElementSummary(t *testing.T) {
	srv := fpltest.NewServer(t)

	// Player 12 is Saka; the saved history covers gameweeks 1-5.
	summary, err := NewClient(srv.URL).FetchElementSummary(context.Background(), 12)
	if err != nil {
		t.Fatalf("FetchElementSummary: %v", err)
	}
	if len(summary.History) != 5 {
		t.Fatalf("history: want 5 matches, got %d", len(summary.History))
	}
	last := summary.History[4]
	if last.Round != 5 || last.OpponentTeam != 5 || last.WasHome || last.Minutes != 80 || last.ExpectedGoals != "0.34" {
		t.Errorf("gameweek 5 entry: got %+v", last)
	}
}

func TestFetch_SendsUserAgent(t *testing.T) {
	// A tiny one-off server that records the User-Agent it receives.
	var gotUA string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotUA = r.Header.Get("User-Agent")
		w.Write([]byte(`[]`))
	}))
	defer srv.Close()

	if _, err := NewClient(srv.URL).FetchFixtures(context.Background()); err != nil {
		t.Fatalf("FetchFixtures: %v", err)
	}
	if !strings.HasPrefix(gotUA, "Mozilla/5.0") {
		t.Errorf("want a browser-like User-Agent, got %q", gotUA)
	}
}

func TestFetch_ErrorStatus(t *testing.T) {
	srv := fpltest.NewServer(t)
	client := NewClient(srv.URL)

	// No saved history for player 99999, so the fake answers 404 like the real API.
	_, err := client.FetchElementSummary(context.Background(), 99999)
	if !errors.Is(err, ErrNotFound) {
		t.Errorf("unknown player: want ErrNotFound, got %v", err)
	}

	srv.SetDown(true)
	_, err = client.FetchBootstrap(context.Background())
	if err == nil || !strings.Contains(err.Error(), "503") {
		t.Errorf("API down: want a 503 error, got %v", err)
	}
}

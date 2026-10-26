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

func TestFetchEventLive(t *testing.T) {
	srv := fpltest.NewServer(t)

	live, err := NewClient(srv.URL).FetchEventLive(context.Background(), 1)
	if err != nil {
		t.Fatalf("FetchEventLive: %v", err)
	}
	// Every player registered by gameweek 1, not just the ones who played.
	if len(live.Elements) != 610 {
		t.Fatalf("want 610 players, got %d", len(live.Elements))
	}

	// Saka (12) in gameweek 1: 67 minutes, 1 goal, 9 points, 0.64 xG in fixture 1.
	for _, p := range live.Elements {
		if p.ID != 12 {
			continue
		}
		if p.Stats.Minutes != 67 || p.Stats.GoalsScored != 1 || p.Stats.TotalPoints != 9 || p.Stats.ExpectedGoals != "0.64" {
			t.Errorf("Saka gameweek 1 totals: got %+v", p.Stats)
		}
		if len(p.Explain) != 1 || p.Explain[0].Fixture != 1 {
			t.Fatalf("want one match (fixture 1), got %+v", p.Explain)
		}
		// The per-match breakdown adds up to the gameweek totals.
		if p.Explain[0].Minutes() != 67 || p.Explain[0].Points() != 9 {
			t.Errorf("fixture 1: want 67 minutes and 9 points, got %d and %d", p.Explain[0].Minutes(), p.Explain[0].Points())
		}
	}
}

func TestLiveExplain_ZeroMinutes(t *testing.T) {
	// A match he sat out still appears, with a "minutes" line of 0.
	e := LiveExplain{Fixture: 42, Stats: []LiveExplainStat{{Identifier: "minutes", Value: 0, Points: 0}}}
	if e.Minutes() != 0 || e.Points() != 0 {
		t.Errorf("want 0 minutes and 0 points, got %d and %d", e.Minutes(), e.Points())
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

	// No saved data for gameweek 39, so the fake answers 404 like the real API.
	_, err := client.FetchEventLive(context.Background(), 39)
	if !errors.Is(err, ErrNotFound) {
		t.Errorf("unknown gameweek: want ErrNotFound, got %v", err)
	}

	srv.SetDown(true)
	_, err = client.FetchBootstrap(context.Background())
	if err == nil || !strings.Contains(err.Error(), "503") {
		t.Errorf("API down: want a 503 error, got %v", err)
	}
}

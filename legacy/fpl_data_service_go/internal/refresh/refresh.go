// Package refresh decides when cached FPL data is stale and re-downloads it.
// main.go runs All on startup and on a timer, so requests are answered from
// SQLite without waiting on the FPL API. There are three kinds of download:
//
//	bootstrap   teams, gameweeks, chips, players   GET /bootstrap-static/
//	fixtures    every fixture of the season        GET /fixtures/
//	gameweeks   every player's stats, per week     GET /event/{gw}/live/
//
// Each is skipped if sync_log says it was saved recently enough.
package refresh

import (
	"context"
	"fmt"
	"log/slog"
	"math"
	"time"

	"squad_scout/fpl_data_service/internal/db"
	"squad_scout/fpl_data_service/internal/fpl"
)

// never is a maximum age that's never reached: data saved once stays fresh.
const never = time.Duration(math.MaxInt64)

// Refresher downloads FPL data into the store when the stored copy is older
// than maxAge.
type Refresher struct {
	store  *db.Store
	client *fpl.Client
	maxAge time.Duration
}

// New returns a Refresher that treats data older than maxAge as stale.
func New(store *db.Store, client *fpl.Client, maxAge time.Duration) *Refresher {
	return &Refresher{store: store, client: client, maxAge: maxAge}
}

// All refreshes everything, in dependency order: bootstrap first (the rest
// refer to its teams, players and gameweeks), then fixtures (the gameweek
// stats are joined with them), then gameweek stats. If one step fails, the
// later ones are skipped and the error is returned.
func (r *Refresher) All(ctx context.Context) error {
	if err := r.Bootstrap(ctx); err != nil {
		return err
	}
	if err := r.Fixtures(ctx); err != nil {
		return err
	}
	return r.Gameweeks(ctx)
}

// Bootstrap re-downloads teams, gameweeks, chips and players if they're stale.
func (r *Refresher) Bootstrap(ctx context.Context) error {
	return r.ifStale(ctx, db.DatasetBootstrap, r.maxAge, func(ctx context.Context) error {
		b, err := r.client.FetchBootstrap(ctx)
		if err != nil {
			return err
		}
		if err := r.store.UpsertBootstrap(ctx, b); err != nil {
			return err
		}
		slog.Info("bootstrap refreshed", "players", len(b.Elements), "teams", len(b.Teams), "gameweeks", len(b.Events))
		return nil
	})
}

// Fixtures re-downloads every fixture if they're stale.
func (r *Refresher) Fixtures(ctx context.Context) error {
	return r.ifStale(ctx, db.DatasetFixtures, r.maxAge, func(ctx context.Context) error {
		fixtures, err := r.client.FetchFixtures(ctx)
		if err != nil {
			return err
		}
		if err := r.store.UpsertFixtures(ctx, fixtures); err != nil {
			return err
		}
		slog.Info("fixtures refreshed", "fixtures", len(fixtures))
		return nil
	})
}

// Gameweeks downloads every player's stats for each gameweek played so far.
//
// The current gameweek and the one before it can still change (points come
// in during matches; bonus points and corrections arrive a day or so later),
// so those two are refreshed like everything else. Older gameweeks are final:
// each is downloaded once and kept.
//
// Each gameweek is saved and recorded separately, so if the API fails half
// way, the next run carries on from where this one stopped.
func (r *Refresher) Gameweeks(ctx context.Context) error {
	current, ok, err := r.store.CurrentGameweek(ctx)
	if err != nil {
		return err
	}
	if !ok {
		return nil // the season hasn't started: nothing played yet
	}

	for gw := 1; gw <= current; gw++ {
		maxAge := r.maxAge
		if gw < current-1 {
			maxAge = never
		}
		err := r.ifStale(ctx, db.GameweekStatsDataset(gw), maxAge, func(ctx context.Context) error {
			live, err := r.client.FetchEventLive(ctx, gw)
			if err != nil {
				return err
			}
			if err := r.store.UpsertGameweekStats(ctx, gw, live); err != nil {
				return err
			}
			slog.Info("gameweek stats refreshed", "gameweek", gw, "players", len(live.Elements))
			return nil
		})
		if err != nil {
			return err
		}
	}
	return nil
}

// ifStale runs download only if dataset was never saved, or was last saved
// maxAge or more ago. It is the one place the freshness rule lives.
//
// download is a function passed in as a value: each caller hands over "how
// to download my dataset", and ifStale decides whether to call it.
func (r *Refresher) ifStale(ctx context.Context, dataset string, maxAge time.Duration, download func(context.Context) error) error {
	last, ok, err := r.store.LastSynced(ctx, dataset)
	if err != nil {
		return fmt.Errorf("check age of %s: %w", dataset, err)
	}
	if ok && time.Since(last) < maxAge {
		// Debug, not Info: this happens on every timer tick, and the default
		// logger hides Debug lines.
		slog.Debug("fresh, skipping download", "dataset", dataset, "synced_at", last)
		return nil
	}
	if err := download(ctx); err != nil {
		return fmt.Errorf("refresh %s: %w", dataset, err)
	}
	return nil
}

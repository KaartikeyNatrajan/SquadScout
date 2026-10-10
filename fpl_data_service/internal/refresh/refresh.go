// Package refresh decides when cached FPL data is stale and re-downloads it.
//
// There are three kinds of download:
//
//	bootstrap        teams, gameweeks, players   refreshed on a timer (main.go)
//	fixtures         every fixture               refreshed on a timer (main.go)
//	player history   one player's matches        refreshed when a request needs it
//
// Each is skipped if sync_log says it was downloaded less than maxAge ago.
package refresh

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"squad_scout/fpl_data_service/internal/db"
	"squad_scout/fpl_data_service/internal/fpl"
)

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

// All refreshes the datasets kept up to date on a timer. Bootstrap goes
// first because fixtures reference its teams; if it fails, fixtures are
// skipped too.
func (r *Refresher) All(ctx context.Context) error {
	if err := r.Bootstrap(ctx); err != nil {
		return err
	}
	return r.Fixtures(ctx)
}

// Bootstrap re-downloads teams, gameweeks and players if they're stale.
func (r *Refresher) Bootstrap(ctx context.Context) error {
	return r.ifStale(ctx, db.DatasetBootstrap, func(ctx context.Context) error {
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
	return r.ifStale(ctx, db.DatasetFixtures, func(ctx context.Context) error {
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

// PlayerHistory re-downloads one player's match history if it's stale.
// The player must already be stored (Bootstrap).
//
// If two requests for the same player arrive at once, both may download;
// that's harmless because the second save simply replaces the first.
func (r *Refresher) PlayerHistory(ctx context.Context, playerID int) error {
	return r.ifStale(ctx, db.PlayerHistoryDataset(playerID), func(ctx context.Context) error {
		summary, err := r.client.FetchElementSummary(ctx, playerID)
		if err != nil {
			return err
		}
		if err := r.store.UpsertPlayerHistory(ctx, playerID, summary.History); err != nil {
			return err
		}
		slog.Info("player history refreshed", "player_id", playerID, "matches", len(summary.History))
		return nil
	})
}

// ifStale runs download only if dataset was never downloaded or was last
// downloaded maxAge or more ago. It is the one place the freshness rule lives.
//
// download is a function passed in as a value: each caller hands over "how
// to download my dataset", and ifStale decides whether to call it.
func (r *Refresher) ifStale(ctx context.Context, dataset string, download func(context.Context) error) error {
	last, ok, err := r.store.LastSynced(ctx, dataset)
	if err != nil {
		return fmt.Errorf("check age of %s: %w", dataset, err)
	}
	if ok && time.Since(last) < r.maxAge {
		// Debug, not Info: this happens on every timer tick and many requests,
		// and the default logger hides Debug lines.
		slog.Debug("fresh, skipping download", "dataset", dataset, "synced_at", last)
		return nil
	}
	if err := download(ctx); err != nil {
		return fmt.Errorf("refresh %s: %w", dataset, err)
	}
	return nil
}

package db

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"
)

// Dataset names used as keys in sync_log. Each one is one download from the
// FPL API; the refresh package uses them to decide what is stale.
const (
	DatasetBootstrap = "bootstrap"
	DatasetFixtures  = "fixtures"
)

// PlayerHistoryDataset is the sync_log key for one player's match history.
// Each player is downloaded (and goes stale) separately.
func PlayerHistoryDataset(playerID int) string {
	return fmt.Sprintf("element-summary/%d", playerID)
}

// LastSynced returns when dataset was last downloaded. ok is false if it
// never has been, which is a normal situation rather than an error.
func (s *Store) LastSynced(ctx context.Context, dataset string) (t time.Time, ok bool, err error) {
	var syncedAt string
	query := "SELECT synced_at FROM sync_log WHERE dataset = ?"
	err = s.db.QueryRowContext(ctx, query, dataset).Scan(&syncedAt)
	// No row: never synced.
	if errors.Is(err, sql.ErrNoRows) {
		return time.Time{}, false, nil
	}
	if err != nil {
		return time.Time{}, false, fmt.Errorf("read sync log %s: %w", dataset, err)
	}
	t, err = time.Parse(time.RFC3339, syncedAt)
	if err != nil {
		return time.Time{}, false, fmt.Errorf("parse sync time %s: %w", dataset, err)
	}
	return t, true, nil
}

// recordSync marks dataset as downloaded now. It takes the caller's
// transaction, so the sync is only recorded if the data itself saves too:
// sync_log can never claim a download that didn't make it into the tables.
func recordSync(ctx context.Context, tx *sql.Tx, dataset string) error {
	_, err := tx.ExecContext(ctx, `
		INSERT INTO sync_log (dataset, synced_at) VALUES (?, ?)
		ON CONFLICT(dataset) DO UPDATE SET synced_at = excluded.synced_at`,
		dataset, time.Now().UTC().Format(time.RFC3339),
	)
	if err != nil {
		return fmt.Errorf("record sync of %s: %w", dataset, err)
	}
	return nil
}

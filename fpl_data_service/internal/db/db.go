// Package db owns the SQLite cache. The schema is embedded in the binary and
// applied on startup, so there is no separate migration step.
package db

import (
	"context"
	"database/sql"
	_ "embed"
	"fmt"
	"os"
	"path/filepath"

	_ "modernc.org/sqlite" // pure-Go driver, no CGO/gcc needed
)

//go:embed schema.sql
var schema string

type Store struct {
	db *sql.DB
}

func Open(ctx context.Context, path string) (*Store, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, fmt.Errorf("create db dir: %w", err)
	}

	// WAL lets reads proceed while a refresh job is writing.
	dsn := fmt.Sprintf("file:%s?_pragma=journal_mode(WAL)&_pragma=foreign_keys(ON)&_pragma=busy_timeout(5000)", path)
	sqlDB, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	if err := sqlDB.PingContext(ctx); err != nil {
		return nil, err
	}
	if _, err := sqlDB.ExecContext(ctx, schema); err != nil {
		return nil, fmt.Errorf("apply schema: %w", err)
	}
	return &Store{db: sqlDB}, nil
}

func (s *Store) Close() error { return s.db.Close() }

// TODO: UpsertBootstrap(ctx, *fpl.Bootstrap), SearchPlayers(ctx, name, limit),
// FixturesForTeam(ctx, teamID, nextN), PlayerGameweekStats(ctx, playerID, gw)

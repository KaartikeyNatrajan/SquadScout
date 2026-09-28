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

// The //go:embed line below is a compiler instruction, not a normal comment:
// it copies the text of schema.sql into this variable at build time.
//
//go:embed schema.sql
var schema string

// Store wraps the database connection. The query methods (see TODO at the
// bottom) will hang off it, so the rest of the code never writes raw SQL.
type Store struct {
	db *sql.DB
}

// Open creates the database file if needed, connects, and creates the tables.
func Open(ctx context.Context, path string) (*Store, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, fmt.Errorf("create db dir: %w", err)
	}

	// The DSN (connection string) also sets SQLite options ("pragmas"):
	//   journal_mode(WAL)   see below
	//   foreign_keys(ON)    enforce the REFERENCES rules in schema.sql
	//   busy_timeout(5000)  wait up to 5 s for a lock instead of failing at once
	// WAL lets reads proceed while a refresh job is writing.
	dsn := fmt.Sprintf("file:%s?_pragma=journal_mode(WAL)&_pragma=foreign_keys(ON)&_pragma=busy_timeout(5000)", path)
	sqlDB, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	// sql.Open doesn't actually connect; Ping does, so errors show up here.
	if err := sqlDB.PingContext(ctx); err != nil {
		return nil, err
	}
	// Run every CREATE TABLE IF NOT EXISTS statement in schema.sql.
	if _, err := sqlDB.ExecContext(ctx, schema); err != nil {
		return nil, fmt.Errorf("apply schema: %w", err)
	}
	return &Store{db: sqlDB}, nil
}

// `(s *Store)` makes Close a method on Store, called as store.Close().
func (s *Store) Close() error { return s.db.Close() }

// TODO: UpsertBootstrap(ctx, *fpl.Bootstrap), SearchPlayers(ctx, name, limit),
// FixturesForTeam(ctx, teamID, nextN), PlayerGameweekStats(ctx, playerID, gw)

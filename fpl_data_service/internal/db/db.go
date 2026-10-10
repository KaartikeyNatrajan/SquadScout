// Package db owns the SQLite cache. The schema is embedded in the binary and
// applied on startup, so there is no separate migration step.
//
// Files in this package, one per kind of data:
//
//	db.go          opening the database, schema versioning
//	sync_log.go    when each dataset was last downloaded
//	bootstrap.go   teams, gameweeks and players (from /bootstrap-static/)
//	players.go     player lookups
//	gameweeks.go   gameweek lookups
//	fixtures.go    fixtures (from /fixtures/)
//	history.go     per-match player stats (from /element-summary/{id}/)
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

// schemaVersion must be increased whenever schema.sql changes. See resetIfOutdated.
const schemaVersion = 2

// allTables lists every table this service has ever created, children before
// parents, so dropping them in this order never breaks a REFERENCES rule.
// Keep old names here after renaming a table, so old databases get cleaned up.
var allTables = []string{
	"player_gameweek_stats", // version 1 name of player_match_stats
	"player_match_stats",
	"fixtures",
	"players",
	"events",
	"teams",
	"sync_log",
}

// Store wraps the database connection. Every query lives in a method on it,
// so the rest of the code never writes raw SQL.
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
		sqlDB.Close()
		return nil, err
	}
	if err := resetIfOutdated(ctx, sqlDB); err != nil {
		sqlDB.Close()
		return nil, err
	}
	// Run every CREATE TABLE IF NOT EXISTS statement in schema.sql.
	if _, err := sqlDB.ExecContext(ctx, schema); err != nil {
		sqlDB.Close()
		return nil, fmt.Errorf("apply schema: %w", err)
	}
	// Remember which schema these tables were made from. PRAGMA values can't
	// be ? parameters, hence Sprintf (safe: schemaVersion is our own constant).
	if _, err := sqlDB.ExecContext(ctx, fmt.Sprintf("PRAGMA user_version = %d", schemaVersion)); err != nil {
		sqlDB.Close()
		return nil, fmt.Errorf("set schema version: %w", err)
	}
	return &Store{db: sqlDB}, nil
}

// resetIfOutdated drops every table if the database was created from an
// older schema.sql. The next step in Open then creates them from scratch.
//
// SQLite keeps a free integer in each database file, "user_version", for
// exactly this purpose. It starts at 0, and Open sets it to schemaVersion.
// Dropping is fine because this database is only a cache: the refresh job
// downloads everything again.
func resetIfOutdated(ctx context.Context, sqlDB *sql.DB) error {
	var version int
	if err := sqlDB.QueryRowContext(ctx, "PRAGMA user_version").Scan(&version); err != nil {
		return fmt.Errorf("read schema version: %w", err)
	}
	if version == schemaVersion {
		return nil
	}
	for _, table := range allTables {
		// Table names can't be ? parameters; safe because they're our own constants.
		if _, err := sqlDB.ExecContext(ctx, "DROP TABLE IF EXISTS "+table); err != nil {
			return fmt.Errorf("drop %s: %w", table, err)
		}
	}
	return nil
}

// `(s *Store)` makes Close a method on Store, called as store.Close().
func (s *Store) Close() error { return s.db.Close() }

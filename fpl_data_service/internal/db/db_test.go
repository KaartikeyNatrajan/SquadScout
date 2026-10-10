package db

import (
	"context"
	"path/filepath"
	"testing"
)

// An existing database made from an older schema must be wiped and rebuilt,
// not reused with missing columns.
func TestOpen_ResetsOutdatedSchema(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "test.db")

	// Create a current database with some data in it...
	store, err := Open(ctx, path)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	if err := store.UpsertBootstrap(ctx, loadBootstrap(t)); err != nil {
		t.Fatalf("upsert: %v", err)
	}
	// ...then pretend it was made by an older version of the code.
	if _, err := store.db.Exec("PRAGMA user_version = 1"); err != nil {
		t.Fatalf("set old version: %v", err)
	}
	store.Close()

	store, err = Open(ctx, path)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	defer store.Close()

	if got := countRows(t, store, "players"); got != 0 {
		t.Errorf("players after reset: want 0, got %d", got)
	}
	var version int
	if err := store.db.QueryRow("PRAGMA user_version").Scan(&version); err != nil {
		t.Fatalf("read version: %v", err)
	}
	if version != schemaVersion {
		t.Errorf("user_version: want %d, got %d", schemaVersion, version)
	}
}

// Reopening a current database must keep its data.
func TestOpen_KeepsCurrentSchema(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "test.db")

	store, err := Open(ctx, path)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	if err := store.UpsertBootstrap(ctx, loadBootstrap(t)); err != nil {
		t.Fatalf("upsert: %v", err)
	}
	store.Close()

	store, err = Open(ctx, path)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	defer store.Close()

	if got := countRows(t, store, "players"); got != 667 {
		t.Errorf("players after reopen: want 667, got %d", got)
	}
}

package db

// Helpers shared by this package's tests. A _test.go file's helpers are only
// visible to tests in the same package, so other packages have their own.

import (
	"context"
	"encoding/json"
	"fmt"
	"path/filepath"
	"testing"

	"squad_scout/fpl_data_service/internal/fpl"
	"squad_scout/fpl_data_service/internal/fpltest"
)

// openTestStore opens a fresh database in a temporary folder that Go deletes
// after the test.
func openTestStore(t *testing.T) *Store {
	t.Helper() // report failures at the caller's line, not here
	store, err := Open(context.Background(), filepath.Join(t.TempDir(), "test.db"))
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { store.Close() })
	return store
}

// decodeTestdata decodes a saved FPL response into dst.
func decodeTestdata(t *testing.T, name string, dst any) {
	t.Helper()
	if err := json.Unmarshal(fpltest.ReadFile(t, name), dst); err != nil {
		t.Fatalf("decode %s: %v", name, err)
	}
}

func loadBootstrap(t *testing.T) *fpl.Bootstrap {
	t.Helper()
	var b fpl.Bootstrap
	decodeTestdata(t, "bootstrap-static.json", &b)
	return &b
}

func loadFixtures(t *testing.T) []fpl.Fixture {
	t.Helper()
	var f []fpl.Fixture
	decodeTestdata(t, "fixtures.json", &f)
	return f
}

// loadGameweek returns every player's saved stats for one gameweek (1-5).
func loadGameweek(t *testing.T, gameweek int) *fpl.EventLive {
	t.Helper()
	var live fpl.EventLive
	decodeTestdata(t, fmt.Sprintf("event-%d-live.json", gameweek), &live)
	return &live
}

// seededStore returns a fresh store with the saved bootstrap and fixtures.
func seededStore(t *testing.T) *Store {
	t.Helper()
	ctx := context.Background()
	store := openTestStore(t)
	if err := store.UpsertBootstrap(ctx, loadBootstrap(t)); err != nil {
		t.Fatalf("upsert bootstrap: %v", err)
	}
	if err := store.UpsertFixtures(ctx, loadFixtures(t)); err != nil {
		t.Fatalf("upsert fixtures: %v", err)
	}
	return store
}

func countRows(t *testing.T, store *Store, table string) int {
	t.Helper()
	var n int
	// Table names can't be ? parameters; this is safe only because the tests
	// pass fixed names.
	if err := store.db.QueryRow("SELECT COUNT(*) FROM " + table).Scan(&n); err != nil {
		t.Fatalf("count %s: %v", table, err)
	}
	return n
}

// approx reports whether two floats are equal to 2 decimal places. Floats
// can't represent most decimals exactly (0.1 + 0.2 != 0.3), so sums of xG
// values must be compared with a little tolerance.
func approx(a, b float64) bool {
	d := a - b
	return d < 0.005 && d > -0.005
}

package db

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
)

// NextGameweek returns the number of the next gameweek: the one whose
// transfer deadline hasn't passed yet. ok is false once the season is over.
func (s *Store) NextGameweek(ctx context.Context) (gw int, ok bool, err error) {
	err = s.db.QueryRowContext(ctx, "SELECT id FROM events WHERE is_next = 1").Scan(&gw)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, false, nil
	}
	if err != nil {
		return 0, false, fmt.Errorf("next gameweek: %w", err)
	}
	return gw, true, nil
}

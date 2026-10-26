package db

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
)

// GameweekRow is one gameweek ("event" in FPL).
type GameweekRow struct {
	ID       int    // 1-38
	Deadline string // RFC 3339: transfers and team changes lock at this time
}

// NextGameweek returns the next gameweek: the one whose deadline hasn't
// passed yet. ok is false once the season is over.
func (s *Store) NextGameweek(ctx context.Context) (gw GameweekRow, ok bool, err error) {
	err = s.db.QueryRowContext(ctx, "SELECT id, deadline_time FROM events WHERE is_next = 1").Scan(&gw.ID, &gw.Deadline)
	if errors.Is(err, sql.ErrNoRows) {
		return GameweekRow{}, false, nil
	}
	if err != nil {
		return GameweekRow{}, false, fmt.Errorf("next gameweek: %w", err)
	}
	return gw, true, nil
}

// CurrentGameweek returns the gameweek whose deadline passed most recently:
// the one being played now, or just played. ok is false before the season
// starts.
func (s *Store) CurrentGameweek(ctx context.Context) (gw int, ok bool, err error) {
	err = s.db.QueryRowContext(ctx, "SELECT id FROM events WHERE is_current = 1").Scan(&gw)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, false, nil
	}
	if err != nil {
		return 0, false, fmt.Errorf("current gameweek: %w", err)
	}
	return gw, true, nil
}

// Deadline returns the transfer deadline of a gameweek (RFC 3339). ok is
// false if there's no such gameweek.
func (s *Store) Deadline(ctx context.Context, gameweek int) (deadline string, ok bool, err error) {
	err = s.db.QueryRowContext(ctx, "SELECT deadline_time FROM events WHERE id = ?", gameweek).Scan(&deadline)
	if errors.Is(err, sql.ErrNoRows) {
		return "", false, nil
	}
	if err != nil {
		return "", false, fmt.Errorf("deadline of gameweek %d: %w", gameweek, err)
	}
	return deadline, true, nil
}

// ChipRow is one chip allowance: the chip may be played once between
// StartEvent and StopEvent (inclusive).
type ChipRow struct {
	Name       string // "wildcard", "freehit", "bboost", "3xc"
	StartEvent int
	StopEvent  int
}

// Chips returns every chip allowance this season, in id order.
func (s *Store) Chips(ctx context.Context) ([]ChipRow, error) {
	rows, err := s.db.QueryContext(ctx, "SELECT name, start_event, stop_event FROM chips ORDER BY id")
	if err != nil {
		return nil, fmt.Errorf("list chips: %w", err)
	}
	defer rows.Close()

	var out []ChipRow
	for rows.Next() {
		var c ChipRow
		if err := rows.Scan(&c.Name, &c.StartEvent, &c.StopEvent); err != nil {
			return nil, fmt.Errorf("scan chip: %w", err)
		}
		out = append(out, c)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("list chips: %w", err)
	}
	return out, nil
}

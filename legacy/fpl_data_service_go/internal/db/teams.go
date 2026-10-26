package db

import (
	"context"
	"fmt"
)

// TeamRow is one Premier League club.
type TeamRow struct {
	ID        int
	Name      string // "Arsenal", "Man Utd", "Nott'm Forest"
	ShortName string // "ARS", "MUN", "NFO"
}

// Teams returns all stored teams in id order (20 in a season).
func (s *Store) Teams(ctx context.Context) ([]TeamRow, error) {
	rows, err := s.db.QueryContext(ctx, "SELECT id, name, short_name FROM teams ORDER BY id")
	if err != nil {
		return nil, fmt.Errorf("list teams: %w", err)
	}
	defer rows.Close()

	var out []TeamRow
	for rows.Next() {
		var t TeamRow
		if err := rows.Scan(&t.ID, &t.Name, &t.ShortName); err != nil {
			return nil, fmt.Errorf("scan team: %w", err)
		}
		out = append(out, t)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("list teams: %w", err)
	}
	return out, nil
}

// TeamExists reports whether a team with this id is stored.
func (s *Store) TeamExists(ctx context.Context, id int) (bool, error) {
	// EXISTS returns 1 or 0, which scans straight into a bool.
	var exists bool
	err := s.db.QueryRowContext(ctx, "SELECT EXISTS (SELECT 1 FROM teams WHERE id = ?)", id).Scan(&exists)
	if err != nil {
		return false, fmt.Errorf("check team %d: %w", id, err)
	}
	return exists, nil
}

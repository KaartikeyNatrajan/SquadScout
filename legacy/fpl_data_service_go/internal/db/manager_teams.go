package db

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"
)

// The db package stores managers' teams without knowing their shape: the
// manager package turns a team into JSON and back. That keeps the import
// direction one-way (manager uses db, never the other way round).

// CachedTeam is a stored manager's team.
type CachedTeam struct {
	Data       []byte    // JSON, as written by the manager package
	FetchedAt  time.Time // when it was downloaded
	ValidUntil time.Time // the deadline after which the squad may have changed
}

// SaveManagerTeam stores a team's JSON, replacing any older copy.
func (s *Store) SaveManagerTeam(ctx context.Context, teamID int, data []byte, fetchedAt, validUntil time.Time) error {
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO manager_teams (team_id, data, fetched_at, valid_until)
		VALUES (?, ?, ?, ?)
		ON CONFLICT(team_id) DO UPDATE SET
			data = excluded.data,
			fetched_at = excluded.fetched_at,
			valid_until = excluded.valid_until`,
		teamID, string(data),
		fetchedAt.UTC().Format(time.RFC3339), validUntil.UTC().Format(time.RFC3339),
	)
	if err != nil {
		return fmt.Errorf("save team %d: %w", teamID, err)
	}
	return nil
}

// LoadManagerTeam returns a stored team, fresh or not; the caller decides
// using ValidUntil. ok is false if the team was never stored.
func (s *Store) LoadManagerTeam(ctx context.Context, teamID int) (team CachedTeam, ok bool, err error) {
	var data, fetchedAt, validUntil string
	err = s.db.QueryRowContext(ctx,
		"SELECT data, fetched_at, valid_until FROM manager_teams WHERE team_id = ?", teamID,
	).Scan(&data, &fetchedAt, &validUntil)
	if errors.Is(err, sql.ErrNoRows) {
		return CachedTeam{}, false, nil
	}
	if err != nil {
		return CachedTeam{}, false, fmt.Errorf("load team %d: %w", teamID, err)
	}

	team.Data = []byte(data)
	if team.FetchedAt, err = time.Parse(time.RFC3339, fetchedAt); err != nil {
		return CachedTeam{}, false, fmt.Errorf("team %d fetched_at: %w", teamID, err)
	}
	if team.ValidUntil, err = time.Parse(time.RFC3339, validUntil); err != nil {
		return CachedTeam{}, false, fmt.Errorf("team %d valid_until: %w", teamID, err)
	}
	return team, true, nil
}

package db

import (
	"context"
	"fmt"

	"squad_scout/fpl_data_service/internal/fpl"
)

// FixtureRow is one fixture with both team names filled in.
type FixtureRow struct {
	ID             int
	Gameweek       int    // 0 if not scheduled yet
	KickoffTime    string // RFC 3339; "" if not scheduled yet
	HomeTeamID     int
	HomeTeamName   string
	AwayTeamID     int
	AwayTeamName   string
	HomeDifficulty int // FDR 1 (easy) - 5 (hard), for the home team
	AwayDifficulty int
	Finished       bool
	HomeScore      *int // nil until played
	AwayScore      *int
}

// UpsertFixtures saves every fixture from /fixtures/ and records the sync,
// all-or-nothing. Teams must be stored first (UpsertBootstrap): fixtures
// reference them.
func (s *Store) UpsertFixtures(ctx context.Context, fixtures []fpl.Fixture) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback()

	stmt, err := tx.PrepareContext(ctx, `
		INSERT INTO fixtures (
			id, gameweek, home_team_id, away_team_id, kickoff_time,
			home_difficulty, away_difficulty, finished, home_score, away_score
		)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			gameweek = excluded.gameweek,
			home_team_id = excluded.home_team_id,
			away_team_id = excluded.away_team_id,
			kickoff_time = excluded.kickoff_time,
			home_difficulty = excluded.home_difficulty,
			away_difficulty = excluded.away_difficulty,
			finished = excluded.finished,
			home_score = excluded.home_score,
			away_score = excluded.away_score`)
	if err != nil {
		return fmt.Errorf("prepare fixtures: %w", err)
	}
	defer stmt.Close()

	for _, f := range fixtures {
		// Event, KickoffTime and the scores are pointers: nil is stored as NULL.
		_, err := stmt.ExecContext(ctx,
			f.ID, f.Event, f.TeamH, f.TeamA, f.KickoffTime,
			f.TeamHDifficulty, f.TeamADifficulty, f.Finished, f.TeamHScore, f.TeamAScore,
		)
		if err != nil {
			return fmt.Errorf("upsert fixture %d: %w", f.ID, err)
		}
	}

	if err := recordSync(ctx, tx, DatasetFixtures); err != nil {
		return err
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit fixtures: %w", err)
	}
	return nil
}

// UpcomingFixtures returns a team's unfinished fixtures, soonest first.
// limit <= 0 means all of them.
func (s *Store) UpcomingFixtures(ctx context.Context, teamID, limit int) ([]FixtureRow, error) {
	if limit <= 0 {
		limit = -1 // SQLite's way of saying "no limit"
	}

	// The fixtures table is joined twice, once per team, to get both names;
	// the aliases h (home) and a (away) tell the two copies apart.
	// COALESCE(x, fallback) turns NULL into the fallback, so unscheduled
	// fixtures scan into plain int/string fields.
	// "kickoff_time IS NULL" sorts as 0 for scheduled fixtures and 1 for
	// unscheduled ones, so undated fixtures go last instead of first.
	const query = `
		SELECT f.id, COALESCE(f.gameweek, 0), COALESCE(f.kickoff_time, ''),
		       f.home_team_id, h.name, f.away_team_id, a.name,
		       f.home_difficulty, f.away_difficulty, f.finished,
		       f.home_score, f.away_score
		FROM fixtures f
		JOIN teams h ON h.id = f.home_team_id
		JOIN teams a ON a.id = f.away_team_id
		WHERE (f.home_team_id = ? OR f.away_team_id = ?) AND f.finished = 0
		ORDER BY f.kickoff_time IS NULL, f.kickoff_time, f.id
		LIMIT ?`

	rows, err := s.db.QueryContext(ctx, query, teamID, teamID, limit)
	if err != nil {
		return nil, fmt.Errorf("upcoming fixtures for team %d: %w", teamID, err)
	}
	defer rows.Close()

	var out []FixtureRow
	for rows.Next() {
		var f FixtureRow
		err := rows.Scan(
			&f.ID, &f.Gameweek, &f.KickoffTime,
			&f.HomeTeamID, &f.HomeTeamName, &f.AwayTeamID, &f.AwayTeamName,
			&f.HomeDifficulty, &f.AwayDifficulty, &f.Finished,
			&f.HomeScore, &f.AwayScore,
		)
		if err != nil {
			return nil, fmt.Errorf("scan fixture: %w", err)
		}
		out = append(out, f)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("upcoming fixtures for team %d: %w", teamID, err)
	}
	return out, nil
}

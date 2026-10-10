package db

import (
	"context"
	"fmt"

	"squad_scout/fpl_data_service/internal/fpl"
	"squad_scout/fpl_data_service/internal/names"
)

// UpsertBootstrap saves the teams, gameweeks and players from
// /bootstrap-static/ and records the sync. It is all-or-nothing: if any row
// fails, nothing is saved.
//
// "Upsert" = insert the row, or update it if a row with that id exists, so
// running this again simply refreshes the data.
func (s *Store) UpsertBootstrap(ctx context.Context, b *fpl.Bootstrap) error {
	// One transaction for everything: all-or-nothing, and much faster than
	// committing each of the ~700 rows separately.
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin tx: %w", err)
	}
	// Undo everything if we return early with an error. After a successful
	// Commit this does nothing.
	defer tx.Rollback()

	// Teams first: players.team_id REFERENCES teams(id), so each player's
	// team must already exist.
	teamsStmt, err := tx.PrepareContext(ctx, `
		INSERT INTO teams (id, name, short_name)
		VALUES (?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			name = excluded.name,
			short_name = excluded.short_name`)
	if err != nil {
		return fmt.Errorf("prepare teams: %w", err)
	}
	// Deferred only after the error check: if Prepare failed, the statement is
	// nil and closing it would panic.
	defer teamsStmt.Close()

	for _, t := range b.Teams {
		if _, err := teamsStmt.ExecContext(ctx, t.ID, t.Name, t.ShortName); err != nil {
			return fmt.Errorf("upsert team %d: %w", t.ID, err)
		}
	}

	// Gameweeks ("events" in FPL). Go bools are stored as 0/1 by the driver.
	eventsStmt, err := tx.PrepareContext(ctx, `
		INSERT INTO events (id, name, deadline_time, finished, is_current, is_next)
		VALUES (?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			name = excluded.name,
			deadline_time = excluded.deadline_time,
			finished = excluded.finished,
			is_current = excluded.is_current,
			is_next = excluded.is_next`)
	if err != nil {
		return fmt.Errorf("prepare events: %w", err)
	}
	defer eventsStmt.Close()

	for _, e := range b.Events {
		if _, err := eventsStmt.ExecContext(ctx, e.ID, e.Name, e.Deadline, e.IsFinished, e.IsCurrent, e.IsNext); err != nil {
			return fmt.Errorf("upsert gameweek %d: %w", e.ID, err)
		}
	}

	// Players. The column list, the ?s and the ExecContext arguments below
	// must stay in the same order. updated_at is set in the UPDATE branch
	// because a column DEFAULT only applies on INSERT.
	playersStmt, err := tx.PrepareContext(ctx, `
		INSERT INTO players (
			id, web_name, first_name, second_name, search_name, team_id, position,
			now_cost, total_points, form, selected_by_percent, status, news,
			chance_of_playing_next_round,
			minutes, starts, goals_scored, assists, clean_sheets, bonus,
			expected_goals, expected_assists, expected_goal_involvements,
			points_per_game, ep_next
		)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			web_name = excluded.web_name,
			first_name = excluded.first_name,
			second_name = excluded.second_name,
			search_name = excluded.search_name,
			team_id = excluded.team_id,
			position = excluded.position,
			now_cost = excluded.now_cost,
			total_points = excluded.total_points,
			form = excluded.form,
			selected_by_percent = excluded.selected_by_percent,
			status = excluded.status,
			news = excluded.news,
			chance_of_playing_next_round = excluded.chance_of_playing_next_round,
			minutes = excluded.minutes,
			starts = excluded.starts,
			goals_scored = excluded.goals_scored,
			assists = excluded.assists,
			clean_sheets = excluded.clean_sheets,
			bonus = excluded.bonus,
			expected_goals = excluded.expected_goals,
			expected_assists = excluded.expected_assists,
			expected_goal_involvements = excluded.expected_goal_involvements,
			points_per_game = excluded.points_per_game,
			ep_next = excluded.ep_next,
			updated_at = datetime('now')`)
	if err != nil {
		return fmt.Errorf("prepare players: %w", err)
	}
	defer playersStmt.Close()

	for _, p := range b.Elements {
		// FPL sends these numbers as strings ("6.2"); see parse.go.
		var fp floatParser
		form := fp.parse("form", p.Form)
		selectedBy := fp.parse("selected_by_percent", p.SelectedByPercent)
		xG := fp.parse("expected_goals", p.ExpectedGoals)
		xA := fp.parse("expected_assists", p.ExpectedAssists)
		xGI := fp.parse("expected_goal_involvements", p.ExpectedGoalInvolvements)
		pointsPerGame := fp.parse("points_per_game", p.PointsPerGame)
		epNext := fp.parse("ep_next", p.EPNext)
		if fp.err != nil {
			return fmt.Errorf("player %d: %w", p.ID, fp.err)
		}

		// All three names, so "bukayo", "saka" and "magalhaes" all match.
		searchName := names.Normalize(p.FirstName + " " + p.SecondName + " " + p.WebName)

		// ChanceOfPlayingNextRound is a *int: database/sql stores a nil
		// pointer as NULL ("no concerns") and otherwise the value it points to.
		_, err = playersStmt.ExecContext(ctx,
			p.ID, p.WebName, p.FirstName, p.SecondName, searchName, p.Team, p.ElementType,
			p.NowCost, p.TotalPoints, form, selectedBy, p.Status, p.News,
			p.ChanceOfPlayingNextRound,
			p.Minutes, p.Starts, p.GoalsScored, p.Assists, p.CleanSheets, p.Bonus,
			xG, xA, xGI,
			pointsPerGame, epNext,
		)
		if err != nil {
			return fmt.Errorf("upsert player %d: %w", p.ID, err)
		}
	}

	if err := recordSync(ctx, tx, DatasetBootstrap); err != nil {
		return err
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit bootstrap: %w", err)
	}
	return nil
}

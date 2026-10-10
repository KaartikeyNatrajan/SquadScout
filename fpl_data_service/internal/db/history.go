package db

import (
	"context"
	"fmt"

	"squad_scout/fpl_data_service/internal/fpl"
)

// MatchRow is one player's stats in one match, with the opponent's name
// filled in. Scores are from the player's side: TeamScore is his team's goals.
type MatchRow struct {
	PlayerID                 int
	FixtureID                int
	Gameweek                 int
	OpponentTeamID           int
	OpponentTeamName         string
	WasHome                  bool
	KickoffTime              string
	TeamScore                *int // nil if the match hasn't started
	OpponentScore            *int
	Minutes                  int
	Starts                   int
	GoalsScored              int
	Assists                  int
	CleanSheets              int
	GoalsConceded            int
	Saves                    int
	Bonus                    int
	YellowCards              int
	RedCards                 int
	TotalPoints              int
	ExpectedGoals            float64
	ExpectedAssists          float64
	ExpectedGoalInvolvements float64
}

// UpsertPlayerHistory replaces one player's stored match history with
// history (from /element-summary/{id}/) and records the sync, all-or-nothing.
// The player must already be stored (UpsertBootstrap).
//
// It deletes and re-inserts instead of upserting row by row, so matches that
// FPL removed or corrected can't linger. Readers never see the table half
// rewritten: other connections only see the result once the transaction commits.
func (s *Store) UpsertPlayerHistory(ctx context.Context, playerID int, history []fpl.HistoryEntry) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback()

	if _, err := tx.ExecContext(ctx, "DELETE FROM player_match_stats WHERE player_id = ?", playerID); err != nil {
		return fmt.Errorf("clear history of player %d: %w", playerID, err)
	}

	stmt, err := tx.PrepareContext(ctx, `
		INSERT INTO player_match_stats (
			player_id, fixture_id, gameweek, opponent_team_id, was_home, kickoff_time,
			team_score, opponent_score,
			minutes, starts, goals_scored, assists, clean_sheets, goals_conceded,
			saves, bonus, yellow_cards, red_cards, total_points,
			expected_goals, expected_assists, expected_goal_involvements
		)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
	if err != nil {
		return fmt.Errorf("prepare history: %w", err)
	}
	defer stmt.Close()

	for _, h := range history {
		var fp floatParser // see parse.go
		xG := fp.parse("expected_goals", h.ExpectedGoals)
		xA := fp.parse("expected_assists", h.ExpectedAssists)
		xGI := fp.parse("expected_goal_involvements", h.ExpectedGoalInvolvements)
		if fp.err != nil {
			return fmt.Errorf("player %d fixture %d: %w", playerID, h.Fixture, fp.err)
		}

		// FPL gives the score as home-away. Turn it into the player's side,
		// so readers don't have to check was_home themselves.
		teamScore, opponentScore := h.TeamHScore, h.TeamAScore
		if !h.WasHome {
			teamScore, opponentScore = h.TeamAScore, h.TeamHScore
		}

		_, err := stmt.ExecContext(ctx,
			playerID, h.Fixture, h.Round, h.OpponentTeam, h.WasHome, h.KickoffTime,
			teamScore, opponentScore,
			h.Minutes, h.Starts, h.GoalsScored, h.Assists, h.CleanSheets, h.GoalsConceded,
			h.Saves, h.Bonus, h.YellowCards, h.RedCards, h.TotalPoints,
			xG, xA, xGI,
		)
		if err != nil {
			return fmt.Errorf("insert player %d fixture %d: %w", playerID, h.Fixture, err)
		}
	}

	if err := recordSync(ctx, tx, PlayerHistoryDataset(playerID)); err != nil {
		return err
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit history of player %d: %w", playerID, err)
	}
	return nil
}

// matchSelect is the SELECT shared by the match queries below. Its columns
// must match the order in scanMatches.
const matchSelect = `
	SELECT m.player_id, m.fixture_id, m.gameweek, m.opponent_team_id, t.name,
	       m.was_home, m.kickoff_time, m.team_score, m.opponent_score,
	       m.minutes, m.starts, m.goals_scored, m.assists, m.clean_sheets,
	       m.goals_conceded, m.saves, m.bonus, m.yellow_cards, m.red_cards,
	       m.total_points, m.expected_goals, m.expected_assists,
	       m.expected_goal_involvements
	FROM player_match_stats m
	JOIN teams t ON t.id = m.opponent_team_id`

// RecentMatches returns a player's last n matches, most recent first.
// Matches he didn't play in are included (with Minutes == 0).
func (s *Store) RecentMatches(ctx context.Context, playerID, n int) ([]MatchRow, error) {
	return s.queryMatches(ctx, matchSelect+`
		WHERE m.player_id = ?
		ORDER BY m.kickoff_time DESC, m.fixture_id DESC
		LIMIT ?`, playerID, n)
}

// GameweekMatches returns a player's matches in one gameweek, in kickoff
// order: usually one, two in a double gameweek, none in a blank gameweek or
// one that hasn't been played yet.
func (s *Store) GameweekMatches(ctx context.Context, playerID, gameweek int) ([]MatchRow, error) {
	return s.queryMatches(ctx, matchSelect+`
		WHERE m.player_id = ? AND m.gameweek = ?
		ORDER BY m.kickoff_time, m.fixture_id`, playerID, gameweek)
}

// queryMatches runs a matchSelect query and scans every row.
// args ...any accepts any number of values, passed on to QueryContext.
func (s *Store) queryMatches(ctx context.Context, query string, args ...any) ([]MatchRow, error) {
	rows, err := s.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, fmt.Errorf("query matches: %w", err)
	}
	defer rows.Close()

	var out []MatchRow
	for rows.Next() {
		var m MatchRow
		err := rows.Scan(
			&m.PlayerID, &m.FixtureID, &m.Gameweek, &m.OpponentTeamID, &m.OpponentTeamName,
			&m.WasHome, &m.KickoffTime, &m.TeamScore, &m.OpponentScore,
			&m.Minutes, &m.Starts, &m.GoalsScored, &m.Assists, &m.CleanSheets,
			&m.GoalsConceded, &m.Saves, &m.Bonus, &m.YellowCards, &m.RedCards,
			&m.TotalPoints, &m.ExpectedGoals, &m.ExpectedAssists,
			&m.ExpectedGoalInvolvements,
		)
		if err != nil {
			return nil, fmt.Errorf("scan match: %w", err)
		}
		out = append(out, m)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("query matches: %w", err)
	}
	return out, nil
}

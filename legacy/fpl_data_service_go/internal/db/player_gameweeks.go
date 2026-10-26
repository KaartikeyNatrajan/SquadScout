package db

import (
	"context"
	"fmt"
	"strings"

	"squad_scout/fpl_data_service/internal/fpl"
)

// PlayerGameweekRow is one player's FPL totals for one gameweek, plus the
// match (or, in a double gameweek, matches) behind them.
type PlayerGameweekRow struct {
	PlayerID                 int
	Gameweek                 int
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
	Matches                  []MatchRow // in kickoff order
}

// MatchRow is one match from the player's side. FPL only splits minutes and
// points per match; the other stats are gameweek totals.
type MatchRow struct {
	FixtureID        int
	OpponentTeamID   int
	OpponentTeamName string
	WasHome          bool
	KickoffTime      string
	TeamScore        *int // his club's goals; nil if the match hasn't started
	OpponentScore    *int
	Minutes          int
	TotalPoints      int
}

// UpsertGameweekStats replaces every player's stats for one gameweek with
// live (from /event/{gw}/live/) and records the sync, all-or-nothing.
// Players and fixtures must be stored first (UpsertBootstrap, UpsertFixtures).
//
// Delete-then-insert rather than upsert: FPL corrects stats after matches,
// and a correction can remove a row (e.g. a fixture moved to another week).
// Readers never see the gameweek half rewritten, because other connections
// only see the result once the transaction commits.
func (s *Store) UpsertGameweekStats(ctx context.Context, gameweek int, live *fpl.EventLive) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback()

	// Each player's club right now, to record which side of each fixture he
	// was on. (FPL's live data doesn't say.)
	clubs := map[int]int{}
	rows, err := tx.QueryContext(ctx, "SELECT id, team_id FROM players")
	if err != nil {
		return fmt.Errorf("read clubs: %w", err)
	}
	for rows.Next() {
		var player, club int
		if err := rows.Scan(&player, &club); err != nil {
			rows.Close()
			return fmt.Errorf("scan club: %w", err)
		}
		clubs[player] = club
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return fmt.Errorf("read clubs: %w", err)
	}

	// Matches first: they reference the gameweek rows.
	for _, table := range []string{"player_gameweek_matches", "player_gameweeks"} {
		if _, err := tx.ExecContext(ctx, "DELETE FROM "+table+" WHERE gameweek = ?", gameweek); err != nil {
			return fmt.Errorf("clear %s for gameweek %d: %w", table, gameweek, err)
		}
	}

	weekStmt, err := tx.PrepareContext(ctx, `
		INSERT INTO player_gameweeks (
			player_id, gameweek, minutes, starts, goals_scored, assists, clean_sheets,
			goals_conceded, saves, bonus, yellow_cards, red_cards, total_points,
			expected_goals, expected_assists, expected_goal_involvements
		)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
	if err != nil {
		return fmt.Errorf("prepare gameweeks: %w", err)
	}
	defer weekStmt.Close()

	matchStmt, err := tx.PrepareContext(ctx, `
		INSERT INTO player_gameweek_matches (player_id, gameweek, fixture_id, team_id, minutes, total_points)
		VALUES (?, ?, ?, ?, ?, ?)`)
	if err != nil {
		return fmt.Errorf("prepare matches: %w", err)
	}
	defer matchStmt.Close()

	for _, p := range live.Elements {
		// No matches: his club had a blank gameweek. Nothing to record.
		if len(p.Explain) == 0 {
			continue
		}
		// Not in the players table (FPL removed him since): skip rather than
		// break the foreign key and lose the whole gameweek.
		club, ok := clubs[p.ID]
		if !ok {
			continue
		}

		var fp floatParser // see parse.go
		xG := fp.parse("expected_goals", p.Stats.ExpectedGoals)
		xA := fp.parse("expected_assists", p.Stats.ExpectedAssists)
		xGI := fp.parse("expected_goal_involvements", p.Stats.ExpectedGoalInvolvements)
		if fp.err != nil {
			return fmt.Errorf("player %d gameweek %d: %w", p.ID, gameweek, fp.err)
		}

		st := p.Stats
		_, err := weekStmt.ExecContext(ctx,
			p.ID, gameweek, st.Minutes, st.Starts, st.GoalsScored, st.Assists, st.CleanSheets,
			st.GoalsConceded, st.Saves, st.Bonus, st.YellowCards, st.RedCards, st.TotalPoints,
			xG, xA, xGI,
		)
		if err != nil {
			return fmt.Errorf("insert player %d gameweek %d: %w", p.ID, gameweek, err)
		}

		for _, m := range p.Explain {
			if _, err := matchStmt.ExecContext(ctx, p.ID, gameweek, m.Fixture, club, m.Minutes(), m.Points()); err != nil {
				return fmt.Errorf("insert player %d fixture %d: %w", p.ID, m.Fixture, err)
			}
		}
	}

	if err := recordSync(ctx, tx, GameweekStatsDataset(gameweek)); err != nil {
		return err
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit gameweek %d: %w", gameweek, err)
	}
	return nil
}

// gameweekSelect is the SELECT shared by the queries below. Its columns must
// match the order in queryGameweeks.
const gameweekSelect = `
	SELECT player_id, gameweek, minutes, starts, goals_scored, assists, clean_sheets,
	       goals_conceded, saves, bonus, yellow_cards, red_cards, total_points,
	       expected_goals, expected_assists, expected_goal_involvements
	FROM player_gameweeks`

// RecentGameweeks returns a player's last n gameweeks, most recent first,
// each with its matches. Gameweeks he didn't play in are included (Minutes
// 0); gameweeks his club didn't play in aren't.
func (s *Store) RecentGameweeks(ctx context.Context, playerID, n int) ([]PlayerGameweekRow, error) {
	return s.queryGameweeks(ctx, playerID,
		gameweekSelect+" WHERE player_id = ? ORDER BY gameweek DESC LIMIT ?", playerID, n)
}

// PlayerGameweek returns a player's stats for one gameweek. ok is false if
// there are none: not played yet, a blank gameweek, or before he joined.
func (s *Store) PlayerGameweek(ctx context.Context, playerID, gameweek int) (PlayerGameweekRow, bool, error) {
	rows, err := s.queryGameweeks(ctx, playerID,
		gameweekSelect+" WHERE player_id = ? AND gameweek = ?", playerID, gameweek)
	if err != nil || len(rows) == 0 {
		return PlayerGameweekRow{}, false, err
	}
	return rows[0], true, nil
}

// queryGameweeks runs a gameweekSelect query for one player and attaches
// each row's matches.
func (s *Store) queryGameweeks(ctx context.Context, playerID int, query string, args ...any) ([]PlayerGameweekRow, error) {
	rows, err := s.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, fmt.Errorf("query gameweeks: %w", err)
	}
	defer rows.Close()

	var out []PlayerGameweekRow
	for rows.Next() {
		var g PlayerGameweekRow
		err := rows.Scan(
			&g.PlayerID, &g.Gameweek, &g.Minutes, &g.Starts, &g.GoalsScored, &g.Assists, &g.CleanSheets,
			&g.GoalsConceded, &g.Saves, &g.Bonus, &g.YellowCards, &g.RedCards, &g.TotalPoints,
			&g.ExpectedGoals, &g.ExpectedAssists, &g.ExpectedGoalInvolvements,
		)
		if err != nil {
			return nil, fmt.Errorf("scan gameweek: %w", err)
		}
		out = append(out, g)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("query gameweeks: %w", err)
	}

	if err := s.attachMatches(ctx, playerID, out); err != nil {
		return nil, err
	}
	return out, nil
}

// matchSelect reads one player's matches in the given gameweeks, from his
// side. The "sides" step (a CTE: a named sub-query used by the main query)
// works out once whether he was the home team; the main query then uses
// that to pick his opponent and order the score his way round. IIF(x, a, b)
// is SQLite's "if x then a else b".
const matchSelect = `
	WITH sides AS (
		SELECT m.gameweek, m.fixture_id, m.minutes, m.total_points,
		       f.home_team_id = m.team_id AS was_home,
		       f.home_team_id, f.away_team_id, f.home_score, f.away_score,
		       COALESCE(f.kickoff_time, '') AS kickoff_time
		FROM player_gameweek_matches m
		JOIN fixtures f ON f.id = m.fixture_id
		WHERE m.player_id = ? AND m.gameweek IN (%s)
	)
	SELECT gameweek, fixture_id, minutes, total_points, was_home,
	       IIF(was_home, away_team_id, home_team_id), t.name, kickoff_time,
	       IIF(was_home, home_score, away_score),
	       IIF(was_home, away_score, home_score)
	FROM sides
	JOIN teams t ON t.id = IIF(was_home, away_team_id, home_team_id)
	ORDER BY kickoff_time, fixture_id`

// attachMatches fills in Matches for each of a player's gameweek rows, with
// one query for all of them.
func (s *Store) attachMatches(ctx context.Context, playerID int, weeks []PlayerGameweekRow) error {
	if len(weeks) == 0 {
		return nil
	}
	// One "?" per gameweek; the values are still passed as parameters.
	args := []any{playerID}
	for _, w := range weeks {
		args = append(args, w.Gameweek)
	}
	placeholders := strings.TrimSuffix(strings.Repeat("?,", len(weeks)), ",")

	rows, err := s.db.QueryContext(ctx, fmt.Sprintf(matchSelect, placeholders), args...)
	if err != nil {
		return fmt.Errorf("query matches: %w", err)
	}
	defer rows.Close()

	byGameweek := map[int][]MatchRow{}
	for rows.Next() {
		var gameweek int
		var m MatchRow
		err := rows.Scan(
			&gameweek, &m.FixtureID, &m.Minutes, &m.TotalPoints, &m.WasHome,
			&m.OpponentTeamID, &m.OpponentTeamName, &m.KickoffTime,
			&m.TeamScore, &m.OpponentScore,
		)
		if err != nil {
			return fmt.Errorf("scan match: %w", err)
		}
		byGameweek[gameweek] = append(byGameweek[gameweek], m)
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("query matches: %w", err)
	}

	// weeks[i] (not a copy from range) so the change sticks.
	for i := range weeks {
		weeks[i].Matches = byGameweek[weeks[i].Gameweek]
	}
	return nil
}

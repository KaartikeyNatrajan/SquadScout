package db

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"squad_scout/fpl_data_service/internal/names"
)

// PlayerRow is one player as stored in SQLite, joined with the team name.
// The db package returns its own type instead of pb.Player so it doesn't
// depend on gRPC; the handlers convert.
type PlayerRow struct {
	ID                       int
	WebName                  string
	FirstName                string
	SecondName               string
	TeamID                   int
	TeamName                 string
	Position                 int // 1 GK .. 4 FWD
	NowCost                  int // tenths of £m
	TotalPoints              int
	Form                     float64
	SelectedByPercent        float64
	Status                   string // a, d, i, s, u, n
	News                     string
	ChanceOfPlayingNextRound *int // nil = no concerns
	Minutes                  int
	Starts                   int
	GoalsScored              int
	Assists                  int
	CleanSheets              int
	Bonus                    int
	ExpectedGoals            float64
	ExpectedAssists          float64
	ExpectedGoalInvolvements float64
	PointsPerGame            float64
	ExpectedPointsNext       float64 // FPL's ep_next
}

// playerSelect is the SELECT shared by every player query; each query adds
// its own WHERE / ORDER BY. Its columns must match the order in scanPlayer.
const playerSelect = `
	SELECT p.id, p.web_name, p.first_name, p.second_name, p.team_id, t.name,
	       p.position, p.now_cost, p.total_points, p.form, p.selected_by_percent,
	       p.status, p.news, p.chance_of_playing_next_round,
	       p.minutes, p.starts, p.goals_scored, p.assists, p.clean_sheets, p.bonus,
	       p.expected_goals, p.expected_assists, p.expected_goal_involvements,
	       p.points_per_game, p.ep_next
	FROM players p
	JOIN teams t ON t.id = p.team_id`

// scanner is anything with a Scan method: both *sql.Row (one result) and
// *sql.Rows (a loop over results) qualify, so scanPlayer works for both.
// This is how Go interfaces work: no "implements" keyword, a type qualifies
// simply by having the right methods.
type scanner interface {
	Scan(dest ...any) error
}

// scanPlayer reads one row of playerSelect into a PlayerRow.
func scanPlayer(row scanner) (PlayerRow, error) {
	var p PlayerRow
	// Scan targets must be in the same order as the SELECT columns.
	// &p.ChanceOfPlayingNextRound is a **int: Scan sets it to nil for NULL.
	err := row.Scan(
		&p.ID, &p.WebName, &p.FirstName, &p.SecondName, &p.TeamID, &p.TeamName,
		&p.Position, &p.NowCost, &p.TotalPoints, &p.Form, &p.SelectedByPercent,
		&p.Status, &p.News, &p.ChanceOfPlayingNextRound,
		&p.Minutes, &p.Starts, &p.GoalsScored, &p.Assists, &p.CleanSheets, &p.Bonus,
		&p.ExpectedGoals, &p.ExpectedAssists, &p.ExpectedGoalInvolvements,
		&p.PointsPerGame, &p.ExpectedPointsNext,
	)
	return p, err
}

// SearchPlayers returns players whose name contains name, ignoring case and
// accents, best total points first, at most limit rows. No match is an empty
// result, not an error.
func (s *Store) SearchPlayers(ctx context.Context, name string, limit int) ([]PlayerRow, error) {
	// '%' || ? || '%' wraps the parameter in wildcards: "name contains ?".
	// Sorting by points puts the likeliest match first when several players
	// share a name (Wilson, Gomez, ...).
	query := playerSelect + `
		WHERE p.search_name LIKE '%' || ? || '%'
		ORDER BY p.total_points DESC
		LIMIT ?`

	// Normalise the search text the same way search_name was built, so
	// "Ødegaard" and "odegaard" both match.
	rows, err := s.db.QueryContext(ctx, query, names.Normalize(name), limit)
	if err != nil {
		return nil, fmt.Errorf("search players: %w", err)
	}
	defer rows.Close() // releases the connection; forgetting it leaks it

	var out []PlayerRow
	for rows.Next() {
		p, err := scanPlayer(rows)
		if err != nil {
			return nil, fmt.Errorf("scan player: %w", err)
		}
		out = append(out, p)
	}
	// rows.Next() returns false both at the end and on an error; Err says which.
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("search players: %w", err)
	}
	return out, nil
}

// GetPlayer returns one player by id. ok is false if there's no such player.
func (s *Store) GetPlayer(ctx context.Context, id int) (p PlayerRow, ok bool, err error) {
	p, err = scanPlayer(s.db.QueryRowContext(ctx, playerSelect+" WHERE p.id = ?", id))
	if errors.Is(err, sql.ErrNoRows) {
		return PlayerRow{}, false, nil
	}
	if err != nil {
		return PlayerRow{}, false, fmt.Errorf("get player %d: %w", id, err)
	}
	return p, true, nil
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

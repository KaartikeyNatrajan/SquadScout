package db

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

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
	NewsAdded                string // RFC 3339; "" if no news
	ChanceOfPlayingNextRound *int   // nil = no concerns
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
	TransfersInEvent         int     // by all managers since the last deadline
	TransfersOutEvent        int
	CostChangeEvent          int // tenths of £m since the last deadline
}

// playerSelect is the SELECT shared by every player query; each query adds
// its own WHERE / ORDER BY. Its columns must match the order in scanPlayer.
const playerSelect = `
	SELECT p.id, p.web_name, p.first_name, p.second_name, p.team_id, t.name,
	       p.position, p.now_cost, p.total_points, p.form, p.selected_by_percent,
	       p.status, p.news, COALESCE(p.news_added, ''), p.chance_of_playing_next_round,
	       p.minutes, p.starts, p.goals_scored, p.assists, p.clean_sheets, p.bonus,
	       p.expected_goals, p.expected_assists, p.expected_goal_involvements,
	       p.points_per_game, p.ep_next,
	       p.transfers_in_event, p.transfers_out_event, p.cost_change_event
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
		&p.Status, &p.News, &p.NewsAdded, &p.ChanceOfPlayingNextRound,
		&p.Minutes, &p.Starts, &p.GoalsScored, &p.Assists, &p.CleanSheets, &p.Bonus,
		&p.ExpectedGoals, &p.ExpectedAssists, &p.ExpectedGoalInvolvements,
		&p.PointsPerGame, &p.ExpectedPointsNext,
		&p.TransfersInEvent, &p.TransfersOutEvent, &p.CostChangeEvent,
	)
	return p, err
}

// queryPlayers runs a playerSelect query and scans every row.
func (s *Store) queryPlayers(ctx context.Context, query string, args ...any) ([]PlayerRow, error) {
	rows, err := s.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
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
	return out, rows.Err()
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
	out, err := s.queryPlayers(ctx, query, names.Normalize(name), limit)
	if err != nil {
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

// PlayersByID returns the players with these ids, keyed by id. Ids with no
// player are simply missing from the map; callers check.
func (s *Store) PlayersByID(ctx context.Context, ids []int) (map[int]PlayerRow, error) {
	out := make(map[int]PlayerRow, len(ids))
	if len(ids) == 0 {
		return out, nil
	}
	// One "?" per id: "WHERE p.id IN (?,?,?)". Each value is still passed as
	// a parameter, never pasted into the SQL text.
	placeholders := strings.TrimSuffix(strings.Repeat("?,", len(ids)), ",")
	args := make([]any, len(ids))
	for i, id := range ids {
		args[i] = id
	}

	rows, err := s.queryPlayers(ctx, playerSelect+" WHERE p.id IN ("+placeholders+")", args...)
	if err != nil {
		return nil, fmt.Errorf("players by id: %w", err)
	}
	for _, p := range rows {
		out[p.ID] = p
	}
	return out, nil
}

// PlayerSort is a column FindPlayers can sort by, best first.
type PlayerSort int

const (
	SortExpectedPoints PlayerSort = iota // iota counts up from 0: 0, 1, 2, 3
	SortForm
	SortTotalPoints
	SortPointsPerGame
)

// sortColumns maps each PlayerSort to its SQL. ORDER BY can't take a ?
// parameter, so the column has to go into the SQL text. Looking it up in this
// fixed map (instead of using text from the request) makes that safe: only
// these four strings can ever reach the database.
var sortColumns = map[PlayerSort]string{
	SortExpectedPoints: "p.ep_next",
	SortForm:           "p.form",
	SortTotalPoints:    "p.total_points",
	SortPointsPerGame:  "p.points_per_game",
}

// PlayerFilter says which players FindPlayers returns. Zero values mean "no
// filter", so PlayerFilter{Limit: 5} is the 5 best players overall.
type PlayerFilter struct {
	Position           int   // 1 GK .. 4 FWD; 0 = any
	MaxCost            int   // tenths of £m; 0 = no limit
	IncludeUnavailable bool  // include injured (i), suspended (s), unavailable (u, n)
	ExcludeIDs         []int // e.g. players already in a manager's squad
	Sort               PlayerSort
	Limit              int
}

// FindPlayers returns players matching filter, best first by filter.Sort
// (ties broken by total points).
func (s *Store) FindPlayers(ctx context.Context, filter PlayerFilter) ([]PlayerRow, error) {
	// Build the WHERE clause from whichever filters are set. Each condition
	// has ?s, and its values are appended to args in the same order.
	var conditions []string
	var args []any
	if filter.Position != 0 {
		conditions = append(conditions, "p.position = ?")
		args = append(args, filter.Position)
	}
	if filter.MaxCost > 0 {
		conditions = append(conditions, "p.now_cost <= ?")
		args = append(args, filter.MaxCost)
	}
	if !filter.IncludeUnavailable {
		// Available and doubtful players only.
		conditions = append(conditions, "p.status IN ('a', 'd')")
	}
	if len(filter.ExcludeIDs) > 0 {
		placeholders := strings.TrimSuffix(strings.Repeat("?,", len(filter.ExcludeIDs)), ",")
		conditions = append(conditions, "p.id NOT IN ("+placeholders+")")
		for _, id := range filter.ExcludeIDs {
			args = append(args, id)
		}
	}

	query := playerSelect
	if len(conditions) > 0 {
		query += " WHERE " + strings.Join(conditions, " AND ")
	}
	column, ok := sortColumns[filter.Sort]
	if !ok {
		return nil, fmt.Errorf("unknown sort %d", filter.Sort)
	}
	query += " ORDER BY " + column + " DESC, p.total_points DESC LIMIT ?"
	args = append(args, filter.Limit)

	out, err := s.queryPlayers(ctx, query, args...)
	if err != nil {
		return nil, fmt.Errorf("find players: %w", err)
	}
	return out, nil
}

// TopTransfers returns the players most transferred in (in = true) or out
// since the last deadline, most first.
func (s *Store) TopTransfers(ctx context.Context, in bool, limit int) ([]PlayerRow, error) {
	// Same reasoning as sortColumns: the column comes from our own code.
	column := "p.transfers_out_event"
	if in {
		column = "p.transfers_in_event"
	}
	out, err := s.queryPlayers(ctx, playerSelect+" ORDER BY "+column+" DESC LIMIT ?", limit)
	if err != nil {
		return nil, fmt.Errorf("top transfers: %w", err)
	}
	return out, nil
}

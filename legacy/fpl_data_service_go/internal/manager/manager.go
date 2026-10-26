// Package manager loads a manager's FPL team: the squad, bank, free
// transfers, chips left and what each player would sell for.
//
// A team is downloaded the first time it's asked for and then saved in
// SQLite until the next deadline. That's safe because FPL hides a squad's
// new picks until the deadline passes, so nothing we can see changes before
// then (except overall points and rank, which can wait). In practice a
// manager's first question in a gameweek costs 4-5 FPL requests, and every
// later one, even after a restart, costs none.
package manager

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"squad_scout/fpl_data_service/internal/db"
	"squad_scout/fpl_data_service/internal/fpl"
	"squad_scout/fpl_data_service/internal/squad"
)

// Errors callers may want to tell apart (check with errors.Is).
var (
	ErrTeamNotFound = errors.New("no FPL team with that id")
	ErrNoSquadYet   = errors.New("this team hasn't played a gameweek yet, so its squad isn't public")
)

// Team is a manager's team as of the last deadline, ready for the next
// gameweek. Money is in tenths of £m.
type Team struct {
	ID            int
	Name          string
	ManagerName   string
	OverallPoints int
	OverallRank   int // 0 before the first gameweek is scored

	SquadGameweek  int // the gameweek whose squad Picks is
	NextGameweek   int // the gameweek the squad is being prepared for
	Bank           int
	FreeTransfers  int // an estimate, see FreeTransfers
	ChipsAvailable []string
	Picks          []Pick   // in squad position order
	Notes          []string // caveats about the numbers above, in plain words
}

// Pick is one player in the squad.
type Pick struct {
	PlayerID      int
	SquadPosition int // 1-11 starting XI, 12-15 bench
	IsCaptain     bool
	IsViceCaptain bool
	SellingPrice  int
	// True when we don't know what he was bought for, so SellingPrice is
	// just his current price.
	SellingPriceIsEstimate bool
}

// recheckAfter is how long a team is kept when its next deadline is unknown
// or has just passed (FPL takes a few minutes to publish the new squads).
const recheckAfter = 10 * time.Minute

// Loader downloads teams and saves each in the store until its next deadline.
type Loader struct {
	client *fpl.Client
	store  *db.Store // for player prices, chip rules, deadlines and saved teams
	// now is the clock. It's a field so tests can move time past a deadline;
	// everywhere else it's time.Now.
	now func() time.Time
}

// NewLoader returns a Loader that downloads with client and saves to store.
func NewLoader(client *fpl.Client, store *db.Store) *Loader {
	return &Loader{client: client, store: store, now: time.Now}
}

// Load returns the team with this id: the saved copy if its deadline hasn't
// passed, otherwise a fresh download (which is then saved).
//
// If the download fails because FPL is down, an out-of-date saved copy is
// returned with a note saying so: an old squad beats no squad.
func (l *Loader) Load(ctx context.Context, teamID int) (*Team, error) {
	saved, haveSaved, err := l.store.LoadManagerTeam(ctx, teamID)
	if err != nil {
		return nil, err
	}
	if haveSaved && l.now().Before(saved.ValidUntil) {
		return decodeTeam(saved.Data)
	}

	team, err := l.download(ctx, teamID)
	if err != nil {
		// "No such team" is an answer, not an outage: don't paper over it.
		if !haveSaved || errors.Is(err, ErrTeamNotFound) || errors.Is(err, ErrNoSquadYet) {
			return nil, err
		}
		stale, decodeErr := decodeTeam(saved.Data)
		if decodeErr != nil {
			return nil, err
		}
		slog.Warn("team download failed, serving saved copy", "team_id", teamID, "err", err)
		stale.Notes = append(stale.Notes, fmt.Sprintf(
			"FPL couldn't be reached, so this is the copy saved at %s and may be out of date.",
			saved.FetchedAt.Format(time.RFC3339)))
		return stale, nil
	}

	slog.Info("team downloaded", "team_id", teamID, "squad_gameweek", team.SquadGameweek)
	l.save(ctx, team)
	return team, nil
}

// save stores team until its next deadline. A failure is only logged: the
// caller already has the team, and the next request will simply download it
// again.
func (l *Loader) save(ctx context.Context, team *Team) {
	data, err := json.Marshal(team)
	if err != nil {
		slog.Error("encode team", "team_id", team.ID, "err", err)
		return
	}
	now := l.now()
	if err := l.store.SaveManagerTeam(ctx, team.ID, data, now, l.validUntil(ctx, team.NextGameweek)); err != nil {
		slog.Error("save team", "team_id", team.ID, "err", err)
	}
}

// validUntil is when a team downloaded now may next change: the deadline of
// the gameweek it's being prepared for. If that deadline is unknown (season
// over) or already past (FPL is still publishing), check again shortly.
func (l *Loader) validUntil(ctx context.Context, nextGameweek int) time.Time {
	now := l.now()
	deadline, ok, err := l.store.Deadline(ctx, nextGameweek)
	if err != nil || !ok {
		return now.Add(recheckAfter)
	}
	t, err := time.Parse(time.RFC3339, deadline)
	if err != nil || !t.After(now) {
		return now.Add(recheckAfter)
	}
	return t
}

// decodeTeam turns a saved team back into a Team.
func decodeTeam(data []byte) (*Team, error) {
	var team Team
	if err := json.Unmarshal(data, &team); err != nil {
		return nil, fmt.Errorf("decode saved team: %w", err)
	}
	return &team, nil
}

// download fetches everything about a team from the FPL API and works out
// the numbers the API doesn't give directly.
func (l *Loader) download(ctx context.Context, teamID int) (*Team, error) {
	entry, err := l.client.FetchEntry(ctx, teamID)
	if errors.Is(err, fpl.ErrNotFound) {
		return nil, fmt.Errorf("team %d: %w", teamID, ErrTeamNotFound)
	}
	if err != nil {
		return nil, fmt.Errorf("fetch team %d: %w", teamID, err)
	}
	if entry.CurrentEvent == nil {
		return nil, fmt.Errorf("team %d: %w", teamID, ErrNoSquadYet)
	}
	lastDeadline := *entry.CurrentEvent

	team := &Team{
		ID:            entry.ID,
		Name:          entry.Name,
		ManagerName:   entry.PlayerFirstName + " " + entry.PlayerLastName,
		OverallPoints: entry.SummaryOverallPoints,
		SquadGameweek: lastDeadline,
		NextGameweek:  lastDeadline + 1,
	}
	if entry.SummaryOverallRank != nil {
		team.OverallRank = *entry.SummaryOverallRank
	}
	team.Notes = append(team.Notes,
		fmt.Sprintf("FPL only shows a squad after the deadline, so this is the squad at the gameweek %d deadline; transfers made since then are not visible.", lastDeadline),
		"free_transfers is an estimate worked out from the transfer history.",
		fmt.Sprintf("Overall points and rank are as of %s; they update at the next deadline.", l.now().UTC().Format("2 Jan 15:04 MST")),
	)

	picks, err := l.client.FetchEntryPicks(ctx, teamID, lastDeadline)
	if err != nil {
		return nil, fmt.Errorf("fetch picks of team %d: %w", teamID, err)
	}
	// A Free Hit squad lasts one gameweek; afterwards the team goes back to
	// the squad it had before. That earlier squad is the one to plan with.
	if picks.ActiveChip != nil && *picks.ActiveChip == squad.ChipFreeHit && lastDeadline > entry.StartedEvent {
		team.SquadGameweek = lastDeadline - 1
		team.Notes = append(team.Notes, fmt.Sprintf(
			"A Free Hit was played in gameweek %d, so the squad shown is the one from gameweek %d, which the team returns to.",
			lastDeadline, team.SquadGameweek))
		if picks, err = l.client.FetchEntryPicks(ctx, teamID, team.SquadGameweek); err != nil {
			return nil, fmt.Errorf("fetch picks of team %d: %w", teamID, err)
		}
	}

	history, err := l.client.FetchEntryHistory(ctx, teamID)
	if err != nil {
		return nil, fmt.Errorf("fetch history of team %d: %w", teamID, err)
	}
	transfers, err := l.client.FetchEntryTransfers(ctx, teamID)
	if err != nil {
		return nil, fmt.Errorf("fetch transfers of team %d: %w", teamID, err)
	}

	// The bank as it was after the squad's gameweek (after a Free Hit, the
	// money also goes back to what it was before).
	for _, h := range history.Current {
		if h.Event == team.SquadGameweek {
			team.Bank = h.Bank
		}
	}
	team.FreeTransfers = FreeTransfers(entry.StartedEvent, lastDeadline, history.Current, history.Chips)

	chipRules, err := l.store.Chips(ctx)
	if err != nil {
		return nil, err
	}
	team.ChipsAvailable = ChipsAvailable(chipRules, history.Chips, team.NextGameweek)

	if err := l.addPicks(ctx, team, picks.Picks, transfers, history.Chips); err != nil {
		return nil, err
	}
	return team, nil
}

// addPicks fills team.Picks, working out each player's selling price from
// his current price (from the store) and what the team paid for him.
func (l *Loader) addPicks(ctx context.Context, team *Team, picks []fpl.Pick, transfers []fpl.EntryTransfer, chips []fpl.ChipPlay) error {
	ids := make([]int, len(picks))
	for i, p := range picks {
		ids[i] = p.Element
	}
	players, err := l.store.PlayersByID(ctx, ids)
	if err != nil {
		return err
	}

	freeHitEvents := map[int]bool{}
	for _, c := range chips {
		if c.Name == squad.ChipFreeHit {
			freeHitEvents[c.Event] = true
		}
	}
	paid := PurchasePrices(transfers, freeHitEvents)

	anyEstimate := false
	for _, p := range picks {
		player, ok := players[p.Element]
		if !ok {
			// Only happens if FPL added a player since our last refresh.
			return fmt.Errorf("player %d in team %d is not in the cache yet; try again after the next refresh", p.Element, team.ID)
		}
		pick := Pick{
			PlayerID:      p.Element,
			SquadPosition: p.Position,
			IsCaptain:     p.IsCaptain,
			IsViceCaptain: p.IsViceCaptain,
		}
		if purchase, known := paid[p.Element]; known {
			pick.SellingPrice = SellingPrice(purchase, player.NowCost)
		} else {
			pick.SellingPrice = player.NowCost
			pick.SellingPriceIsEstimate = true
			anyEstimate = true
		}
		team.Picks = append(team.Picks, pick)
	}

	if anyEstimate {
		team.Notes = append(team.Notes,
			"Some players have been in the squad since it was created, so their purchase price is unknown; their current price is used as their selling price.")
	}
	return nil
}

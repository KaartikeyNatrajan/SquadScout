// Package manager loads a manager's FPL team: the squad, bank, free
// transfers, chips left and what each player would sell for.
//
// Team data isn't stored in SQLite: it belongs to one manager and only
// changes at a deadline. It's kept in memory for a few minutes instead, so
// an agent asking several questions about one team only downloads it once.
package manager

import (
	"context"
	"errors"
	"fmt"
	"sync"
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

// Loader downloads teams and keeps each in memory for ttl.
type Loader struct {
	client *fpl.Client
	store  *db.Store // for player prices and chip rules
	ttl    time.Duration

	mu    sync.Mutex // guards cache: several requests can run at once
	cache map[int]cachedTeam
}

type cachedTeam struct {
	team    *Team
	fetched time.Time
}

// NewLoader returns a Loader that re-downloads a team once its copy is ttl old.
func NewLoader(client *fpl.Client, store *db.Store, ttl time.Duration) *Loader {
	return &Loader{client: client, store: store, ttl: ttl, cache: map[int]cachedTeam{}}
}

// Load returns the team with this id. The returned Team is shared with other
// callers through the cache, so treat it as read-only.
func (l *Loader) Load(ctx context.Context, teamID int) (*Team, error) {
	l.mu.Lock()
	cached, ok := l.cache[teamID]
	l.mu.Unlock()
	if ok && time.Since(cached.fetched) < l.ttl {
		return cached.team, nil
	}

	// The lock isn't held while downloading: other teams' requests shouldn't
	// wait on this one. Two requests for the same team may both download,
	// which is harmless.
	team, err := l.download(ctx, teamID)
	if err != nil {
		return nil, err
	}

	l.mu.Lock()
	l.cache[teamID] = cachedTeam{team: team, fetched: time.Now()}
	l.mu.Unlock()
	return team, nil
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

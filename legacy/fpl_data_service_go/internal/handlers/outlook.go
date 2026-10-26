package handlers

import (
	"context"

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
	"squad_scout/fpl_data_service/internal/db"
)

// fixtureCache looks up each club's next fixtures at most once per request.
// A squad often has 2-3 players from one club, and they share fixtures.
type fixtureCache struct {
	store  *db.Store
	byClub map[int][]*pb.PlayerFixture
}

func (s *FplService) newFixtureCache() *fixtureCache {
	return &fixtureCache{store: s.store, byClub: map[int][]*pb.PlayerFixture{}}
}

// next returns clubID's next few fixtures, seen from that club's side.
func (c *fixtureCache) next(ctx context.Context, clubID int) ([]*pb.PlayerFixture, error) {
	if fixtures, ok := c.byClub[clubID]; ok {
		return fixtures, nil
	}
	rows, err := c.store.UpcomingFixtures(ctx, clubID, outlookFixtures)
	if err != nil {
		return nil, err
	}
	fixtures := make([]*pb.PlayerFixture, 0, len(rows))
	for _, r := range rows {
		fixtures = append(fixtures, toPlayerFixture(r, clubID))
	}
	c.byClub[clubID] = fixtures
	return fixtures, nil
}

// outlooks turns players into PlayerOutlooks: each player with his club's
// next few fixtures, so the caller can judge him without another call.
func (s *FplService) outlooks(ctx context.Context, players []db.PlayerRow) ([]*pb.PlayerOutlook, error) {
	cache := s.newFixtureCache()
	out := make([]*pb.PlayerOutlook, 0, len(players))
	for _, p := range players {
		fixtures, err := cache.next(ctx, p.TeamID)
		if err != nil {
			return nil, internalError("get fixtures", err)
		}
		out = append(out, &pb.PlayerOutlook{Player: toPlayer(p), NextFixtures: fixtures})
	}
	return out, nil
}

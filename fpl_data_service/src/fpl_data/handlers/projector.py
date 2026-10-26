"""Projecting players' points for a request: gathers a player's recent
gameweeks and his club's fixtures from the database and hands them to the pure
model in projection.py."""

from fpl_data import projection
from fpl_data.db import gameweeks, player_gameweeks
from fpl_data.db.fixtures import FixtureRow
from fpl_data.db.players import PlayerRow
from fpl_data.db.store import Store
from fpl_data.handlers import convert
from fpl_data.handlers.fixture_cache import FixtureCache
from squadscout.v1 import fpl_pb2 as pb

DEFAULT_HORIZON = 5  # gameweeks projected when a request doesn't say
MAX_HORIZON = 10
# Used for a position with no stored gameweeks at all (the start of a season).
FALLBACK_POINTS_PER_90 = 3.5


class Projector:
    """Projects players' points over the next `horizon` gameweeks. Make one per
    request: it remembers each club's fixtures and each player's projection."""

    def __init__(self, store: Store, horizon: int = DEFAULT_HORIZON):
        self._store = store
        self.fixtures = FixtureCache(store)
        self._next = gameweeks.next_gameweek(store)
        self._baselines = player_gameweeks.points_per_90_by_position(store)
        self._done: dict[int, projection.Projection] = {}

        # No next gameweek means the season is over: nothing to project.
        remaining = 38 - self._next.id + 1 if self._next else 0
        self.horizon = max(0, min(horizon, remaining))

    def project(self, player: PlayerRow) -> projection.Projection:
        """`player`'s projected points for the next `horizon` gameweeks."""
        if player.id not in self._done:
            self._done[player.id] = self._project(player)
        return self._done[player.id]

    def _project(self, player: PlayerRow) -> projection.Projection:
        if self._next is None:
            return projection.Projection(())
        history = player_gameweeks.recent_gameweeks(
            self._store, player.id, projection.RECENT_GAMEWEEKS
        )
        past = [
            projection.PastGameweek(g.minutes, g.total_points, matches=max(len(g.matches), 1))
            for g in history
        ]
        upcoming = [
            projection.UpcomingMatch(f.gameweek, _difficulty(f, player.team_id))
            for f in self.fixtures.rows(player.team_id)
        ]
        return projection.project(
            past=past,
            upcoming=upcoming,
            first_gameweek=self._next.id,
            horizon=self.horizon,
            baseline_per_90=self._baselines.get(player.position, FALLBACK_POINTS_PER_90),
            status=player.status,
            chance_of_playing=player.chance_of_playing_next_round,
        )

    def to_pb(self, proj: projection.Projection) -> pb.Projection:
        return pb.Projection(
            horizon=len(proj.gameweeks),
            total=convert.round2(proj.total),
            gameweeks=[
                pb.GameweekProjection(
                    gameweek=g.gameweek,
                    expected_points=convert.round2(g.expected_points),
                    matches=g.matches,
                )
                for g in proj.gameweeks
            ],
        )


def _difficulty(fixture: FixtureRow, team_id: int) -> int:
    """How hard the fixture is for `team_id`'s side."""
    return fixture.home_difficulty if fixture.home_team_id == team_id else fixture.away_difficulty

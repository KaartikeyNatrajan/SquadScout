"""Looking up clubs' fixtures once per request."""

from fpl_data.db import fixtures
from fpl_data.db.fixtures import FixtureRow
from fpl_data.db.store import Store
from fpl_data.handlers import convert
from squadscout.v1 import fpl_pb2 as pb

OUTLOOK_FIXTURES = 3  # next fixtures attached to each player in a list


class FixtureCache:
    """Looks up each club's remaining fixtures at most once per request. A squad
    often has 2-3 players from one club, and they share fixtures."""

    def __init__(self, store: Store):
        self._store = store
        self._by_club: dict[int, list[FixtureRow]] = {}

    def rows(self, club_id: int) -> list[FixtureRow]:
        """All of `club_id`'s unfinished fixtures, soonest first."""
        if club_id not in self._by_club:
            self._by_club[club_id] = fixtures.upcoming_fixtures(self._store, club_id)
        return self._by_club[club_id]

    def next(self, club_id: int) -> list[pb.PlayerFixture]:
        """`club_id`'s next few fixtures, seen from that club's side."""
        return [
            convert.to_player_fixture(r, club_id) for r in self.rows(club_id)[:OUTLOOK_FIXTURES]
        ]

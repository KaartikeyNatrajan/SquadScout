"""GetFixtures and GetPlayerFixtures."""

from fpl_data.db import fixtures, gameweeks
from fpl_data.handlers import convert
from fpl_data.handlers.base import MAX_FIXTURES, ServiceBase, clamp_limit
from squadscout.v1 import fpl_pb2 as pb


class FixtureHandlers(ServiceBase):
    async def get_fixtures(self, request: pb.GetFixturesRequest) -> pb.GetFixturesResponse:
        """A team's next fixtures, soonest first. The team is given by team_id
        or team_name. next_n = 0 means all remaining fixtures."""
        team = self.lookup_team(request.team_id, request.team_name)
        rows = fixtures.upcoming_fixtures(self.store, team.id, request.next_n)
        return pb.GetFixturesResponse(fixtures=[convert.to_fixture(r) for r in rows])

    async def get_player_fixtures(
        self, request: pb.GetPlayerFixturesRequest
    ) -> pb.GetPlayerFixturesResponse:
        """A player's next fixtures from his team's side, plus FPL's expected
        points for him in the next gameweek.

        FPL only publishes expected points for the next gameweek (ep_next), not
        per future fixture, so that's the one projection we pass on. The
        difficulty of each fixture is the best guide beyond that.
        """
        player = self.lookup_player(request.player_id)
        limit = clamp_limit(request.next_n, MAX_FIXTURES)

        rows = fixtures.upcoming_fixtures(self.store, player.team_id, limit)
        # Once the season is over there's no next gameweek; 0 says so.
        next_gameweek = gameweeks.next_gameweek(self.store)
        return pb.GetPlayerFixturesResponse(
            player_id=player.id,
            web_name=player.web_name,
            team_name=player.team_name,
            next_gameweek=next_gameweek.id if next_gameweek else 0,
            expected_points_next=player.expected_points_next,
            fixtures=[convert.to_player_fixture(r, player.team_id) for r in rows],
        )

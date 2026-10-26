"""GetFixtures and GetPlayerFixtures."""

from fpl_data import names
from fpl_data.db import fixtures, gameweeks, teams
from fpl_data.db.teams import TeamRow
from fpl_data.handlers import convert
from fpl_data.handlers.base import MAX_FIXTURES, ServiceBase, clamp_limit
from fpl_data.handlers.errors import invalid_argument, not_found
from squadscout.v1 import fpl_pb2 as pb

# Full club names that people (and models) use, mapped to FPL's names, for the
# clubs where one isn't simply part of the other. Keys are normalised
# (lowercase, no accents).
_TEAM_ALIASES = {
    "manchester city": "Man City",
    "manchester united": "Man Utd",
    "man united": "Man Utd",
    "tottenham": "Spurs",
    "tottenham hotspur": "Spurs",
    "nottingham forest": "Nott'm Forest",
}


class FixtureHandlers(ServiceBase):
    async def get_fixtures(self, request: pb.GetFixturesRequest) -> pb.GetFixturesResponse:
        """A team's next fixtures, soonest first. The team is given by team_id
        or team_name. next_n = 0 means all remaining fixtures."""
        team_id = self._resolve_team(request.team_id, request.team_name)
        rows = fixtures.upcoming_fixtures(self.store, team_id, request.next_n)
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

    def _resolve_team(self, team_id: int, team_name: str) -> int:
        """The id of the team a request names, by id or by name, or a
        ServiceError. An id, if given, wins.

        Names are matched ignoring case and accents, in this order:
        1. the full or short name ("Arsenal", "ARS"), or an alias above;
        2. a team whose name contains the text or is contained in it, if
           exactly one does ("Villa" -> Aston Villa, "Brighton & Hove Albion"
           -> Brighton).
        """
        if team_id < 0:
            raise invalid_argument("team_id must be positive")
        if team_id > 0:
            # Check the team exists, so a wrong id is NOT_FOUND rather than an
            # empty list that looks like "no fixtures left".
            if not teams.team_exists(self.store, team_id):
                raise not_found(f"no team with id {team_id}; try team_name instead")
            return team_id

        team_name = team_name.strip()
        if not team_name:
            raise invalid_argument("team_id or team_name is required")
        all_teams = teams.all_teams(self.store)

        want = names.normalize(team_name)
        want = names.normalize(_TEAM_ALIASES.get(want, want))
        partial: list[TeamRow] = []
        for team in all_teams:
            full, short = names.normalize(team.name), names.normalize(team.short_name)
            if want in (full, short):
                return team.id
            if want in full or full in want:
                partial.append(team)
        if len(partial) == 1:
            return partial[0].id

        # No single match: list the options so the caller can try again.
        if partial:
            raise not_found(
                f'"{team_name}" matches several teams: {", ".join(t.name for t in partial)}'
            )
        raise not_found(
            f'no team called "{team_name}"; the teams are: {", ".join(t.name for t in all_teams)}'
        )

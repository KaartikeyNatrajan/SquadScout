"""What every group of handlers shares: the store, the manager loader, limits,
and lookups that turn a bad id into a ready-made error."""

import logging

import grpc

from fpl_data import names, team_names
from fpl_data.db import players, teams, transfer_snapshots
from fpl_data.db.players import PlayerRow
from fpl_data.db.store import Store
from fpl_data.db.teams import TeamRow
from fpl_data.fpl.client import FplError
from fpl_data.handlers import convert
from fpl_data.handlers.errors import ServiceError, invalid_argument, not_found
from fpl_data.handlers.projector import DEFAULT_HORIZON, Projector
from fpl_data.manager.loader import Loader, ManagerError, NoSquadYetError, Team, TeamNotFoundError
from squadscout.v1 import fpl_pb2 as pb

logger = logging.getLogger(__name__)

# Limits on list sizes. Small lists keep tool results short, which matters for
# a small model with a small context window.
DEFAULT_LIMIT = 5  # when a request leaves its limit at 0
MAX_SEARCH_RESULTS = 20  # SearchPlayers, FindPlayers, GetTransferTrends
MAX_MATCHES = 10  # GetPlayerRecentForm
MAX_FIXTURES = 10  # GetPlayerFixtures


def clamp_limit(requested: int, maximum: int) -> int:
    """Turn a requested list size into one we'll actually use: 0 (or negative)
    means "the default", and anything above `maximum` becomes `maximum`."""
    if requested <= 0:
        return DEFAULT_LIMIT
    return min(requested, maximum)


class ServiceBase:
    def __init__(self, store: Store, managers: Loader):
        self.store = store  # everything except managers' teams; kept fresh by the refresh timer
        self.managers = (
            managers  # managers' teams: downloaded on first request, saved until the deadline
        )

    def lookup_player(self, player_id: int) -> PlayerRow:
        """The player with this id, or a ServiceError: INVALID_ARGUMENT for a
        bad id, NOT_FOUND for an unknown one."""
        if player_id <= 0:
            raise invalid_argument("player_id must be positive")
        player = players.get_player(self.store, player_id)
        if player is None:
            # The hint tells the caller (the model) how to recover.
            raise not_found(f"no player with id {player_id}; use SearchPlayers to find player ids")
        return player

    async def load_team(self, team_id: int) -> Team:
        """A manager's team, or a ServiceError ready to raise."""
        if team_id <= 0:
            raise invalid_argument("team_id must be positive")
        try:
            return await self.managers.load(team_id)
        except TeamNotFoundError:
            raise not_found(
                f"no FPL team with id {team_id}; the id is the number in the URL of the manager's FPL points page"
            ) from None
        except NoSquadYetError as err:
            raise ServiceError(grpc.StatusCode.FAILED_PRECONDITION, str(err)) from err
        except (FplError, ManagerError) as err:
            # Almost always the FPL API being down or slow.
            logger.error("load team %d: %s", team_id, err)
            raise ServiceError(
                grpc.StatusCode.UNAVAILABLE, f"team {team_id} can't be loaded right now"
            ) from err

    def lookup_team(self, team_id: int, team_name: str) -> TeamRow:
        """The team a request names, by id or by name, or a ServiceError.
        An id, if given, wins.

        Names are matched ignoring case and accents, in this order:
        1. its FPL or short name ("Arsenal", "ARS") or another name it goes by
           (team_names.py: "Manchester City", "Tottenham");
        2. a team whose name contains the text or is contained in it, if
           exactly one does ("Villa" -> Aston Villa, "Brighton & Hove Albion"
           -> Brighton).
        """
        if team_id < 0:
            raise invalid_argument("team_id must be positive")
        all_teams = teams.all_teams(self.store)
        if team_id > 0:
            # A wrong id is NOT_FOUND rather than an empty answer that looks like
            # "no fixtures left".
            for team in all_teams:
                if team.id == team_id:
                    return team
            raise not_found(f"no team with id {team_id}; try team_name instead")

        team_name = team_name.strip()
        if not team_name:
            raise invalid_argument("team_id or team_name is required")

        want = names.normalize(team_name)
        partial: list[TeamRow] = []
        for team in all_teams:
            known = {names.normalize(n) for n in team_names.all_names(team.name)}
            if want in known or want == names.normalize(team.short_name):
                return team
            full = names.normalize(team_names.full_name(team.name))
            if want in full or full in want:
                partial.append(team)
        if len(partial) == 1:
            return partial[0]

        # No single match: list the options so the caller can try again.
        if partial:
            raise not_found(
                f'"{team_name}" matches several teams: {", ".join(t.name for t in partial)}'
            )
        raise not_found(
            f'no team called "{team_name}"; the teams are: {", ".join(t.name for t in all_teams)}'
        )

    def recent_transfers(self) -> dict[int, int]:
        """Each player's net transfers over the last 24 hours (empty until the
        service has that much history). Look it up once per request and pass it
        to convert.to_player."""
        return transfer_snapshots.net_transfers_over(self.store)

    def projector(self, horizon: int = DEFAULT_HORIZON) -> Projector:
        """A Projector for this request. Share it between everything one request
        shows, so each club's fixtures and each player's history are looked up once."""
        return Projector(self.store, horizon)

    def outlooks(
        self, rows: list[PlayerRow], projector: Projector | None = None
    ) -> list[pb.PlayerOutlook]:
        """Players with their club's next few fixtures and our projection of
        their points, so the caller can judge each one without another call."""
        projector = projector or self.projector()
        recent = self.recent_transfers()
        return [
            pb.PlayerOutlook(
                player=convert.to_player(p, recent.get(p.id)),
                next_fixtures=projector.fixtures.next(p.team_id),
                projection=projector.to_pb(projector.project(p)),
            )
            for p in rows
        ]

    def squad_players(self, team: Team) -> dict[int, PlayerRow]:
        """Every player in the team's squad, keyed by id."""
        return players.players_by_id(self.store, [p.player_id for p in team.picks])

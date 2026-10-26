"""SearchPlayers and FindPlayers."""

import math

from fpl_data.db import players
from fpl_data.db.players import PlayerFilter, PlayerSort
from fpl_data.handlers import convert
from fpl_data.handlers.base import MAX_SEARCH_RESULTS, ServiceBase, clamp_limit
from fpl_data.handlers.errors import invalid_argument
from squadscout.v1 import fpl_pb2 as pb

# The proto's PlayerSort to the database's. UNSPECIFIED means expected points,
# the most useful default for picking transfers.
_SORT_BY_PROTO = {
    pb.PLAYER_SORT_UNSPECIFIED: PlayerSort.EXPECTED_POINTS,
    pb.PLAYER_SORT_EXPECTED_POINTS: PlayerSort.EXPECTED_POINTS,
    pb.PLAYER_SORT_FORM: PlayerSort.FORM,
    pb.PLAYER_SORT_TOTAL_POINTS: PlayerSort.TOTAL_POINTS,
    pb.PLAYER_SORT_POINTS_PER_GAME: PlayerSort.POINTS_PER_GAME,
}


class PlayerHandlers(ServiceBase):
    async def search_players(self, request: pb.SearchPlayersRequest) -> pb.SearchPlayersResponse:
        """Find players by name, best total points first. Served entirely from
        the data the background refresh keeps up to date."""
        name = request.name.strip()
        if not name:
            raise invalid_argument("name is required")
        limit = clamp_limit(request.limit, MAX_SEARCH_RESULTS)

        rows = players.search(self.store, name, limit)
        # No match is an empty list, not an error: "nobody called that" is a
        # valid answer the caller can act on.
        recent = self.recent_transfers()
        return pb.SearchPlayersResponse(
            players=[convert.to_player(p, recent.get(p.id)) for p in rows]
        )

    async def find_players(self, request: pb.FindPlayersRequest) -> pb.FindPlayersResponse:
        """Players matching the request's filters, best first, each with his
        next fixtures. It's how the agent discovers transfer targets it doesn't
        already know by name."""
        sort = _SORT_BY_PROTO.get(request.sort_by)
        if sort is None:
            raise invalid_argument(f"unknown sort_by {request.sort_by}")
        if request.max_price < 0:
            raise invalid_argument("max_price can't be negative")

        flt = PlayerFilter(
            position=request.position,  # UNSPECIFIED is 0, which means "any"
            # £m to tenths. Round rather than truncate: the caller can send any
            # float, and a value like 8.2999999 should mean £8.3m, not £8.2m.
            max_cost=math.floor(request.max_price * 10 + 0.5),
            include_unavailable=request.include_unavailable,
            sort=sort,
            limit=clamp_limit(request.limit, MAX_SEARCH_RESULTS),
        )

        # Leave out players the manager already owns: they can't be bought.
        if request.exclude_squad_of_team_id != 0:
            team = await self.load_team(request.exclude_squad_of_team_id)
            flt.exclude_ids = [pick.player_id for pick in team.picks]

        rows = players.find_players(self.store, flt)
        return pb.FindPlayersResponse(players=self.outlooks(rows))

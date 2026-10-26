"""GetPlayerRecentForm and GetPlayerGameweekStats."""

import grpc

from fpl_data.db import player_gameweeks
from fpl_data.db.player_gameweeks import PlayerGameweekRow
from fpl_data.handlers import convert
from fpl_data.handlers.base import MAX_MATCHES, ServiceBase, clamp_limit
from fpl_data.handlers.convert import round2
from fpl_data.handlers.errors import ServiceError, invalid_argument
from squadscout.v1 import fpl_pb2 as pb


class StatsHandlers(ServiceBase):
    async def get_player_recent_form(
        self, request: pb.GetPlayerRecentFormRequest
    ) -> pb.GetPlayerRecentFormResponse:
        """A player's last few gameweeks, most recent first, plus totals over
        them. Answered from SQLite: every gameweek's stats are downloaded by the
        background refresh."""
        player = self.lookup_player(request.player_id)
        rows = player_gameweeks.recent_gameweeks(
            self.store, player.id, clamp_limit(request.last_n, MAX_MATCHES)
        )

        # A player with no gameweeks yet (e.g. just signed) gets an empty list
        # and zero totals, not an error.
        return pb.GetPlayerRecentFormResponse(
            player_id=player.id,
            web_name=player.web_name,
            matches=[convert.to_gameweek_stats(r) for r in rows],
            summary=summarize(rows),
        )

    async def get_player_gameweek_stats(
        self, request: pb.GetPlayerGameweekStatsRequest
    ) -> pb.GetPlayerGameweekStatsResponse:
        """A player's stats for one gameweek, with each match his club played
        that week in stats.match_results."""
        if request.gameweek <= 0:
            raise invalid_argument("gameweek must be positive")
        player = self.lookup_player(request.player_id)

        row = player_gameweeks.player_gameweek(self.store, player.id, request.gameweek)
        if row is None:
            raise ServiceError(
                grpc.StatusCode.NOT_FOUND,
                f"{player.web_name} has no stats for gameweek {request.gameweek} "
                "(not played yet, a blank gameweek, or before he joined)",
            )
        return pb.GetPlayerGameweekStatsResponse(stats=convert.to_gameweek_stats(row))


def summarize(rows: list[PlayerGameweekRow]) -> pb.FormSummary:
    """The FormSummary for a run of gameweeks."""
    total_points = sum(g.total_points for g in rows)
    return pb.FormSummary(
        matches=len(rows),
        appearances=sum(1 for g in rows if g.minutes > 0),
        minutes=sum(g.minutes for g in rows),
        goals_scored=sum(g.goals_scored for g in rows),
        assists=sum(g.assists for g in rows),
        clean_sheets=sum(g.clean_sheets for g in rows),
        bonus=sum(g.bonus for g in rows),
        total_points=total_points,
        expected_goals=round2(sum(g.expected_goals for g in rows)),  # a sum of floats, see round2
        expected_assists=round2(sum(g.expected_assists for g in rows)),
        # Guard against dividing by zero when there are no gameweeks.
        points_per_match=round2(total_points / len(rows)) if rows else 0.0,
    )

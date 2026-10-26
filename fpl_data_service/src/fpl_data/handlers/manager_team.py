"""GetManagerTeam."""

from fpl_data.db import gameweeks
from fpl_data.handlers import convert
from fpl_data.handlers.base import ServiceBase
from fpl_data.handlers.convert import pounds, round2
from squadscout.v1 import fpl_pb2 as pb


class ManagerTeamHandlers(ServiceBase):
    async def get_manager_team(
        self, request: pb.GetManagerTeamRequest
    ) -> pb.GetManagerTeamResponse:
        """A manager's squad, each player with his availability, expected
        points and next fixtures, plus the bank, free transfers and chips left.
        One call has everything needed to pick a captain or read the team's
        injury news."""
        team = await self.load_team(request.team_id)
        current = self.squad_players(team)

        projector = self.projector()
        squad_value = 0.0
        squad = []
        for pick in team.picks:
            player = current[pick.player_id]  # Loader checked every pick is stored
            squad_value += pounds(pick.selling_price)
            squad.append(
                pb.SquadPlayer(
                    player=convert.to_player(player),
                    next_fixtures=projector.fixtures.next(player.team_id),
                    squad_position=pick.squad_position,
                    is_captain=pick.is_captain,
                    is_vice_captain=pick.is_vice_captain,
                    selling_price=pounds(pick.selling_price),
                    selling_price_is_estimate=pick.selling_price_is_estimate,
                    projection=projector.to_pb(projector.project(player)),
                )
            )

        response = pb.GetManagerTeamResponse(
            team_id=team.id,
            team_name=team.name,
            manager_name=team.manager_name,
            overall_points=team.overall_points,
            overall_rank=team.overall_rank,
            squad_gameweek=team.squad_gameweek,
            next_gameweek=team.next_gameweek,
            bank=pounds(team.bank),
            squad_value=round2(squad_value),  # a sum of floats, see round2
            free_transfers=team.free_transfers,
            chips_available=team.chips_available,
            players=squad,
            notes=team.notes,
        )

        # The deadline comes from the gameweek table. If our bootstrap is a
        # little behind the team's data they can disagree; then leave it out
        # rather than give the wrong gameweek's deadline.
        upcoming = gameweeks.next_gameweek(self.store)
        if upcoming is not None and upcoming.id == team.next_gameweek:
            response.next_deadline = upcoming.deadline
        return response

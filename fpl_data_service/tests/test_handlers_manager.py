import grpc

from squadscout.v1 import fpl_pb2 as pb
from tests.helpers import error_of

# Team 6 in the saved data: a normal gameweek 5 (two transfers, no chip), £0.1m
# in the bank, 1 free transfer for gameweek 6, only the Free Hit left, Haaland
# (squad position 11) captain. Team 1 played a Free Hit in gameweek 5.


async def test_get_manager_team(service):
    response = await service.get_manager_team(pb.GetManagerTeamRequest(team_id=6))

    assert (response.squad_gameweek, response.next_gameweek) == (5, 6)
    assert response.next_deadline == "2026-10-10T10:00:00Z"
    assert (response.bank, response.free_transfers) == (0.1, 1)
    assert list(response.chips_available) == ["freehit"]
    assert len(response.notes) > 0  # explaining the estimates

    squad = response.players
    assert [p.squad_position for p in squad] == list(range(1, 16))  # in order
    for p in squad:
        assert len(p.next_fixtures) == 3, p.player.web_name
    captain = next(p for p in squad if p.is_captain)
    assert (captain.player.web_name, captain.selling_price) == ("Haaland", 15.5)
    assert response.squad_value == 101.0  # the selling prices add up to 1010 tenths


async def test_after_a_free_hit_the_earlier_squad_is_shown(service):
    response = await service.get_manager_team(pb.GetManagerTeamRequest(team_id=1))
    assert response.squad_gameweek == 4  # the Free Hit squad is gone; gameweek 4's is back


async def test_get_manager_team_errors(service):
    err = await error_of(service.get_manager_team(pb.GetManagerTeamRequest(team_id=0)))
    assert err.code == grpc.StatusCode.INVALID_ARGUMENT
    err = await error_of(service.get_manager_team(pb.GetManagerTeamRequest(team_id=999)))
    assert err.code == grpc.StatusCode.NOT_FOUND


async def test_fpl_down_and_team_not_saved_is_unavailable(service, fake_fpl):
    fake_fpl.down = True
    err = await error_of(service.get_manager_team(pb.GetManagerTeamRequest(team_id=6)))
    assert err.code == grpc.StatusCode.UNAVAILABLE


async def test_every_squad_player_has_a_projection(service):
    response = await service.get_manager_team(pb.GetManagerTeamRequest(team_id=6))

    for p in response.players:
        assert p.projection.horizon == 5, p.player.web_name
        assert len(p.projection.gameweeks) == 5
    totals = {p.player.web_name: p.projection.total for p in response.players}
    assert (
        totals["Haaland"] > totals["Raya"] > totals["Bentley"] == 0
    )  # a star, a starting keeper, a backup keeper
    assert totals["Groß"] > 30  # in form with an easy run

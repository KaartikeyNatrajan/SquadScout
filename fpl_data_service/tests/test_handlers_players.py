import grpc
import pytest

from fpl_data.handlers.base import DEFAULT_LIMIT, MAX_SEARCH_RESULTS
from fpl_data.handlers.service import FplService
from squadscout.v1 import fpl_pb2 as pb
from tests.helpers import error_of

INVALID, NOT_FOUND = grpc.StatusCode.INVALID_ARGUMENT, grpc.StatusCode.NOT_FOUND


async def search(service, name, limit=0):
    return (await service.search_players(pb.SearchPlayersRequest(name=name, limit=limit))).players


async def test_empty_name_is_invalid():
    # No store needed: the handler rejects the name before using it.
    err = await error_of(FplService(None, None).search_players(pb.SearchPlayersRequest(name="  ")))
    assert err.code == INVALID


async def test_finds_saka(service):
    found = await search(service, "saka")

    # Best points first, so the real Saka beats Wan-Bissaka and Sakamoto.
    saka = found[0]
    assert (saka.web_name, saka.team_name, saka.price) == ("Saka", "Arsenal", 9.5)
    assert saka.position == pb.POSITION_MIDFIELDER
    # The fields added for the model: plain-word availability, season totals, FPL's expected points.
    assert (saka.availability, saka.goals_scored, saka.minutes, saka.expected_points_next) == (
        "available",
        3,
        416,
        5.8,
    )
    # No injury concerns: the optional field must be unset, not 0 ("definitely out").
    assert not saka.HasField("chance_of_playing_next_round")


async def test_doubtful_player_has_news_and_chance(service):
    found = await search(service, "benjamin white")

    assert len(found) == 1
    white = found[0]
    assert (white.availability, white.news) == ("doubtful", "Groin injury - 75% chance of playing")
    assert white.HasField("chance_of_playing_next_round")
    assert white.chance_of_playing_next_round == 75


async def test_ignores_accents(service):
    assert (await search(service, "odegaard"))[0].web_name == "Ødegaard"


async def test_no_match_is_an_empty_list(service):
    assert await search(service, "zzzz") == []


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(0, DEFAULT_LIMIT), (2, 2), (100, MAX_SEARCH_RESULTS)],  # 0 = the default; as asked; capped
)
async def test_limits(service, requested, expected):
    # "a" matches hundreds of players, so only the limit decides the count.
    assert len(await search(service, "a", requested)) == expected


async def test_find_players(service):
    # The best midfielders at £5.7m or less by expected points (the default sort).
    request = pb.FindPlayersRequest(position=pb.POSITION_MIDFIELDER, max_price=5.7, limit=5)
    found = (await service.find_players(request)).players

    assert len(found) == 5
    for outlook in found:
        assert outlook.player.position == pb.POSITION_MIDFIELDER
        assert outlook.player.price <= 5.7
        assert len(outlook.next_fixtures) == 3
        # ...and a projection of the next 5 gameweeks, starting with gameweek 6.
        assert [g.gameweek for g in outlook.projection.gameweeks] == [6, 7, 8, 9, 10]
        assert outlook.projection.horizon == 5
        assert outlook.projection.total == pytest.approx(
            sum(g.expected_points for g in outlook.projection.gameweeks), abs=0.03
        )
    expected = [o.player.expected_points_next for o in found]
    assert expected == sorted(expected, reverse=True)


async def test_find_players_leaves_out_a_managers_squad(service):
    # Groß (124) is the top midfielder by expected points, and team 6 owns him.
    request = pb.FindPlayersRequest(
        position=pb.POSITION_MIDFIELDER, limit=20, exclude_squad_of_team_id=6
    )
    found = (await service.find_players(request)).players

    assert 124 not in [o.player.id for o in found]


@pytest.mark.parametrize(
    ("request_message", "code"),
    [
        (pb.FindPlayersRequest(max_price=-1), INVALID),
        (pb.FindPlayersRequest(sort_by=99), INVALID),
        (pb.FindPlayersRequest(exclude_squad_of_team_id=999), NOT_FOUND),
    ],
)
async def test_find_players_bad_requests(service, request_message, code):
    assert (await error_of(service.find_players(request_message))).code == code

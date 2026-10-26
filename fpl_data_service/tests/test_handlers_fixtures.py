import grpc
import pytest

from fpl_data.handlers.base import DEFAULT_LIMIT, MAX_FIXTURES
from squadscout.v1 import fpl_pb2 as pb
from tests.helpers import error_of

# In the saved data Arsenal (team 1) have played gameweeks 1-5. Their next
# three fixtures are:
#
#   51  GW6  Arsenal v Leeds           home difficulty 3, away 5
#   69  GW7  Nott'm Forest v Arsenal   home difficulty 4, away 3
#   71  GW8  Arsenal v Everton         home difficulty 3, away 5


async def test_get_fixtures(service):
    fixtures = (await service.get_fixtures(pb.GetFixturesRequest(team_id=1, next_n=3))).fixtures

    assert [f.id for f in fixtures] == [51, 69, 71]
    first = fixtures[0]
    assert (first.gameweek, first.home_team_name, first.away_team_name) == (6, "Arsenal", "Leeds")
    assert (first.home_difficulty, first.away_difficulty) == (3, 5)
    assert first.kickoff_time == "2026-10-10T11:30:00Z"
    # Not played yet, so no score at all (unset, not 0-0).
    assert not first.HasField("home_score")
    assert not first.HasField("away_score")


async def test_next_n_zero_means_all_remaining(service):
    fixtures = (await service.get_fixtures(pb.GetFixturesRequest(team_id=1))).fixtures
    assert len(fixtures) == 33  # 38 - 5 played


@pytest.mark.parametrize(
    ("name", "club"),
    [
        ("Arsenal", "Arsenal"),
        ("arsenal", "Arsenal"),  # case doesn't matter
        ("ARS", "Arsenal"),  # short name
        ("Villa", "Aston Villa"),  # part of the name
        ("Manchester United", "Man Utd"),  # an alias
        ("Tottenham Hotspur", "Spurs"),
        ("Brighton & Hove Albion", "Brighton"),  # the name contains FPL's
    ],
)
async def test_get_fixtures_by_team_name(service, name, club):
    fixtures = (
        await service.get_fixtures(pb.GetFixturesRequest(team_name=name, next_n=1))
    ).fixtures
    assert club in (fixtures[0].home_team_name, fixtures[0].away_team_name)


async def test_ambiguous_team_name_lists_the_options(service):
    # "Man" could be either Manchester club.
    err = await error_of(service.get_fixtures(pb.GetFixturesRequest(team_name="Man")))
    assert err.code == grpc.StatusCode.NOT_FOUND
    assert "Man City, Man Utd" in err.message


@pytest.mark.parametrize(
    ("request_message", "code"),
    [
        (pb.GetFixturesRequest(team_name="Barcelona"), grpc.StatusCode.NOT_FOUND),
        (pb.GetFixturesRequest(team_id=99), grpc.StatusCode.NOT_FOUND),
        (pb.GetFixturesRequest(), grpc.StatusCode.INVALID_ARGUMENT),
        (pb.GetFixturesRequest(team_id=-1), grpc.StatusCode.INVALID_ARGUMENT),
    ],
)
async def test_get_fixtures_bad_team(service, request_message, code):
    assert (await error_of(service.get_fixtures(request_message))).code == code


async def test_get_player_fixtures(service):
    response = await service.get_player_fixtures(pb.GetPlayerFixturesRequest(player_id=12))

    assert (response.web_name, response.team_name) == ("Saka", "Arsenal")
    assert (response.next_gameweek, response.expected_points_next) == (6, 5.8)
    assert len(response.fixtures) == DEFAULT_LIMIT
    # Seen from Arsenal's side: at home to Leeds, then away at Forest. The
    # difficulty is Arsenal's half of the fixture both times.
    first, second = response.fixtures[0], response.fixtures[1]
    assert (first.opponent_team_name, first.is_home, first.difficulty) == ("Leeds", True, 3)
    assert (second.opponent_team_name, second.is_home, second.difficulty) == (
        "Nott'm Forest",
        False,
        3,
    )


async def test_get_player_fixtures_errors_and_cap(service):
    err = await error_of(service.get_player_fixtures(pb.GetPlayerFixturesRequest(player_id=0)))
    assert err.code == grpc.StatusCode.INVALID_ARGUMENT
    err = await error_of(service.get_player_fixtures(pb.GetPlayerFixturesRequest(player_id=99999)))
    assert err.code == grpc.StatusCode.NOT_FOUND

    response = await service.get_player_fixtures(
        pb.GetPlayerFixturesRequest(player_id=12, next_n=100)
    )
    assert len(response.fixtures) == MAX_FIXTURES

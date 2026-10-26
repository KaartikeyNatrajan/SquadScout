import grpc

from fpl_data.db.player_gameweeks import MatchRow, PlayerGameweekRow
from fpl_data.handlers import convert
from fpl_data.handlers.base import DEFAULT_LIMIT
from fpl_data.handlers.stats import summarize
from squadscout.v1 import fpl_pb2 as pb
from tests.helpers import error_of

# Saka's (player 12) saved gameweeks 1-5:
#
#   GW  opponent        home  score  min  goals  pts  xG
#   1   Coventry City   yes   3-0    67   1      9    0.64
#   2   Aston Villa     no    1-0    90   1      11   0.63
#   3   Chelsea         yes   2-1    89   0      2    0.23
#   4   Sunderland      no    2-0    90   1      8    1.38
#   5   Brighton        no    0-3    80   0      2    0.34


async def test_recent_form(service):
    response = await service.get_player_recent_form(
        pb.GetPlayerRecentFormRequest(player_id=12, last_n=3)
    )

    assert response.web_name == "Saka"
    weeks = response.matches
    assert [w.gameweek for w in weeks] == [5, 4, 3]  # the last 3 gameweeks, most recent first

    # One match that week, so its details are also on the gameweek itself.
    gw5 = weeks[0]
    assert (gw5.opponent_team_name, gw5.was_home, gw5.team_score, gw5.opponent_score) == (
        "Brighton",
        False,
        0,
        3,
    )
    assert [m.minutes for m in gw5.match_results] == [80]

    # Totals over gameweeks 3-5: 259 minutes, 1 goal, 12 points, 1.95 xG.
    s = response.summary
    assert (s.matches, s.appearances, s.minutes, s.goals_scored, s.total_points) == (
        3,
        3,
        259,
        1,
        12,
    )
    assert (s.expected_goals, s.points_per_match) == (1.95, 4)


async def test_player_stats_need_no_fpl_requests(service, fake_fpl):
    # Player stats come from the database alone, so they keep working while FPL is down.
    fake_fpl.down = True
    hits_before = fake_fpl.hits("/event/5/live/")

    response = await service.get_player_recent_form(pb.GetPlayerRecentFormRequest(player_id=12))

    assert len(response.matches) == DEFAULT_LIMIT
    assert fake_fpl.hits("/event/5/live/") == hits_before


async def test_recent_form_unknown_player(service):
    err = await error_of(
        service.get_player_recent_form(pb.GetPlayerRecentFormRequest(player_id=99999))
    )
    assert err.code == grpc.StatusCode.NOT_FOUND


async def test_gameweek_stats(service):
    response = await service.get_player_gameweek_stats(
        pb.GetPlayerGameweekStatsRequest(player_id=12, gameweek=1)
    )

    s = response.stats
    assert (s.opponent_team_name, s.was_home, s.goals_scored, s.total_points, s.minutes) == (
        "Coventry City",
        True,
        1,
        9,
        67,
    )
    assert len(s.match_results) == 1
    assert len(response.matches) == 0  # the deprecated per-match list is no longer filled


async def test_gameweek_stats_errors(service):
    for request, code in [
        (
            pb.GetPlayerGameweekStatsRequest(player_id=12, gameweek=0),
            grpc.StatusCode.INVALID_ARGUMENT,
        ),
        (pb.GetPlayerGameweekStatsRequest(player_id=99999, gameweek=1), grpc.StatusCode.NOT_FOUND),
        (
            pb.GetPlayerGameweekStatsRequest(player_id=12, gameweek=30),
            grpc.StatusCode.NOT_FOUND,
        ),  # not played yet
    ]:
        assert (await error_of(service.get_player_gameweek_stats(request))).code == code


def gameweek(**overrides) -> PlayerGameweekRow:
    fields = dict(
        player_id=12, gameweek=7, minutes=0, starts=0, goals_scored=0, assists=0, clean_sheets=0,
        goals_conceded=0, saves=0, bonus=0, yellow_cards=0, red_cards=0, total_points=0,
        expected_goals=0.0, expected_assists=0.0, expected_goal_involvements=0.0,
    )  # fmt: skip
    return PlayerGameweekRow(**{**fields, **overrides})


# There's no double gameweek in the saved data yet, so these use made-up rows.
def test_a_double_gameweek_keeps_both_matches():
    row = gameweek(
        minutes=150,
        total_points=12,
        matches=[
            MatchRow(70, 13, "Leeds", True, "", None, None, 90, 8),
            MatchRow(71, 9, "Everton", False, "", None, None, 60, 4),
        ],
    )
    stats = convert.to_gameweek_stats(row)

    assert [m.opponent_team_name for m in stats.match_results] == ["Leeds", "Everton"]
    # Two opponents: the single-match fields stay empty.
    assert (stats.opponent_team_name, stats.fixture_id) == ("", 0)


def test_summarize():
    rows = [
        gameweek(minutes=90, goals_scored=1, total_points=8, expected_goals=0.1),
        gameweek(minutes=0, total_points=0, expected_goals=0.2),  # on the bench all week
    ]
    s = summarize(rows)

    assert (s.matches, s.appearances, s.total_points, s.points_per_match) == (2, 1, 8, 4)
    # 0.1 + 0.2 is 0.30000000000000004 in floating point; rounding fixes that.
    assert s.expected_goals == 0.3


def test_summarize_nothing_is_all_zeros():
    s = summarize([])
    assert (s.matches, s.points_per_match) == (0, 0)


def test_round2_rounds_halves_away_from_zero():
    # Python's own round() would give 0.12 for the first and -0.12 for the last.
    assert [convert.round2(x) for x in (0.125, 1.9499999999999997, -4.199999999999999, -0.125)] == [
        0.13,
        1.95,
        -4.2,
        -0.13,
    ]

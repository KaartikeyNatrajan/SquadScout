import pytest

from fpl_data import lineup
from tests.helpers import valid_squad


def squad_points_by_position(**points):
    """e.g. gk=[...], df=[...] -> the mapping best_eleven takes."""
    return {
        1: points.get("gk", []),
        2: points.get("df", []),
        3: points.get("md", []),
        4: points.get("fw", []),
    }


def test_there_are_eight_legal_formations():
    assert sorted(lineup.FORMATIONS) == [
        (3, 4, 3), (3, 5, 2), (4, 3, 3), (4, 4, 2), (4, 5, 1), (5, 2, 3), (5, 3, 2), (5, 4, 1),
    ]  # fmt: skip


def test_bench_players_do_not_count():
    # Every player is worth 1 point except one 10-point forward who, as the 3rd
    # forward, still starts in a 3-4-3 or 4-3-3 (so his 10 counts).
    points = squad_points_by_position(gk=[1, 1], df=[1] * 5, md=[1] * 5, fw=[10, 1, 1])
    assert lineup.best_eleven(points) == 10 + 10  # 10 for him, 10 for the other ten starters

    # The same total in points, but belonging to a goalkeeper who is second choice: he sits.
    points = squad_points_by_position(gk=[1, 10], df=[1] * 5, md=[1] * 5, fw=[1, 1, 1])
    assert lineup.best_eleven(points) == 10 + 10  # the 10 is the first-choice keeper now
    points = squad_points_by_position(gk=[10, 10], df=[1] * 5, md=[1] * 5, fw=[1, 1, 1])
    assert lineup.best_eleven(points) == 10 + 10  # two 10-point keepers: only one can play


def test_the_best_formation_is_chosen():
    # Three great forwards and weak midfielders: 3-4-3 beats 4-5-1.
    points = squad_points_by_position(gk=[2, 0], df=[2] * 5, md=[1] * 5, fw=[9, 9, 9])
    # 3-4-3: 2 + 3*2 + 4*1 + 27 = 39. 5-2-3: 2 + 10 + 2 + 27 = 41 is better still.
    assert lineup.best_eleven(points) == 2 + 10 + 2 + 27


def test_the_eleven_obeys_the_minimums():
    # Midfielders are the only good players, but at least 3 defenders and 1 forward must start.
    points = squad_points_by_position(gk=[0, 0], df=[0] * 5, md=[10] * 5, fw=[0, 0, 0])
    # The most midfielders possible is 5 (3-5-2): 50 points.
    assert lineup.best_eleven(points) == 50


def test_no_goalkeeper_means_no_legal_eleven():
    assert lineup.best_eleven(
        squad_points_by_position(df=[1] * 5, md=[1] * 5, fw=[1] * 3)
    ) == float("-inf")


def test_too_few_outfield_players_means_no_legal_eleven():
    assert lineup.best_eleven(
        squad_points_by_position(gk=[1], df=[1] * 3, md=[1] * 2, fw=[1])
    ) == float("-inf")


def test_squad_points_uses_each_players_own_points():
    squad = valid_squad()
    points = {m.player_id: float(m.player_id) for m in squad.members}  # player i is worth i points
    # Best eleven: GK 2; then the formation giving the most from DEF 7,6,5,4,3 / MID 12,11,10,9,8 / FWD 15,14,13.
    # 3-5-2 gives 7+6+5 + 12+11+10+9+8 + 15+14 = 18 + 50 + 29 = 97, +2 = 99. 3-4-3 gives 18 + 42 + 42 + 2 = 104.
    assert lineup.squad_points(squad.members, points) == pytest.approx(104)

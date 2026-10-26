import pytest

from fpl_data import projection as pj
from fpl_data.projection import PastGameweek, UpcomingMatch


def regular(points=6, weeks=4):
    """A player who plays every minute and scores `points` in each of `weeks` gameweeks, most recent first."""
    return [PastGameweek(minutes=90, points=points, matches=1)] * weeks


# ---- availability ----------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "chance", "expected"),
    [
        ("a", None, 1.0),  # available, no concerns
        ("d", None, 0.75),  # doubtful, FPL gave no percentage
        ("i", None, 0.0),  # injured
        ("s", None, 0.0),  # suspended
        ("u", None, 0.0),  # unavailable
        ("d", 75, 0.75),  # FPL's own chance
        ("i", 0, 0.0),  # ruled out
    ],
)
def test_availability_for_the_next_gameweek(status, chance, expected):
    assert pj.availability(status, chance, weeks_ahead=0) == expected


def test_a_doubtful_player_gets_fitter_over_the_following_weeks():
    chances = [pj.availability("d", 50, weeks) for weeks in range(5)]
    assert chances == pytest.approx(
        [0.5, 2 / 3, 5 / 6, 1.0, 1.0]
    )  # back to full fitness after RECOVERY_WEEKS


def test_a_player_ruled_out_stays_out():
    assert [pj.availability("i", 0, weeks) for weeks in range(5)] == [0.0] * 5


# ---- the building blocks ---------------------------------------------------


def test_difficulty_factor():
    assert [pj.difficulty_factor(d) for d in range(1, 6)] == pytest.approx(
        [1.24, 1.12, 1.0, 0.88, 0.76]
    )


def test_expected_minutes_are_his_recent_average_per_match():
    # 90 and 45 minutes over two matches (one a double gameweek: 135 minutes in 2 matches).
    past = [PastGameweek(135, 8, matches=2), PastGameweek(0, 0, matches=1)]
    assert pj.expected_minutes_per_match(past) == pytest.approx(45.0)  # 135 / 3 matches


def test_expected_minutes_never_exceed_90_per_match():
    assert pj.expected_minutes_per_match([PastGameweek(120, 5, matches=1)]) == 90.0  # extra time


def test_a_player_with_no_history_is_assumed_to_play_part_of_a_match():
    assert pj.expected_minutes_per_match([]) == pj.NO_HISTORY_MINUTES


def test_only_the_most_recent_gameweeks_are_used():
    old_and_new = regular(points=6, weeks=pj.RECENT_GAMEWEEKS) + regular(points=100, weeks=5)
    assert pj.points_per_90(old_and_new, baseline_per_90=6.0) == pytest.approx(
        6.0
    )  # the 100s are ignored


def test_with_little_evidence_the_position_average_dominates():
    one_big_cameo = [
        PastGameweek(minutes=9, points=8, matches=1)
    ]  # 8 points in 9 minutes = 80 per 90!
    rate = pj.points_per_90(one_big_cameo, baseline_per_90=3.0)
    assert 3.0 < rate < 6.0  # nudged up to about 5.5, nowhere near the raw 80


def test_with_plenty_of_evidence_his_own_rate_dominates():
    rate = pj.points_per_90(regular(points=9, weeks=4), baseline_per_90=3.0)
    assert 6.0 < rate < 9.0  # (36 + 3*3) / (4 + 3) = 6.43
    assert rate == pytest.approx((36 + 3.0 * pj.PRIOR_MATCHES) / (4 + pj.PRIOR_MATCHES))


# ---- projecting ---------------------------------------------------------------


def test_a_regular_projects_his_average_per_match():
    result = pj.project(
        past=regular(points=6),
        upcoming=[UpcomingMatch(10, difficulty=3), UpcomingMatch(11, difficulty=3)],
        first_gameweek=10,
        horizon=2,
        baseline_per_90=6.0,  # same as his own rate, so nothing is pulled either way
    )
    assert [g.expected_points for g in result.gameweeks] == pytest.approx([6.0, 6.0])
    assert result.total == pytest.approx(12.0)


def test_double_and_blank_gameweeks():
    result = pj.project(
        past=regular(points=6),
        upcoming=[
            UpcomingMatch(10, 3),
            UpcomingMatch(11, 3),
            UpcomingMatch(11, 3),
        ],  # no match in 12
        first_gameweek=10,
        horizon=3,
        baseline_per_90=6.0,
    )
    assert [(g.gameweek, g.matches) for g in result.gameweeks] == [(10, 1), (11, 2), (12, 0)]
    assert [g.expected_points for g in result.gameweeks] == pytest.approx([6.0, 12.0, 0.0])


def test_fixtures_outside_the_horizon_are_ignored():
    result = pj.project(
        past=regular(), upcoming=[UpcomingMatch(10, 3), UpcomingMatch(14, 3)],
        first_gameweek=10, horizon=2, baseline_per_90=6.0,
    )  # fmt: skip
    assert result.total == pytest.approx(6.0)


def test_an_easy_fixture_is_worth_more_than_a_hard_one():
    def total(difficulty):
        return pj.project(
            past=regular(),
            upcoming=[UpcomingMatch(10, difficulty)],
            first_gameweek=10,
            horizon=1,
            baseline_per_90=6.0,
        ).total

    assert total(1) == pytest.approx(6.0 * 1.24)
    assert total(5) == pytest.approx(6.0 * 0.76)
    assert total(1) > total(3) > total(5)


def test_a_part_time_player_projects_proportionally_fewer_points():
    half_time = [
        PastGameweek(minutes=45, points=3, matches=1)
    ] * 4  # 6 per 90, but only half a match
    result = pj.project(
        past=half_time,
        upcoming=[UpcomingMatch(10, 3)],
        first_gameweek=10,
        horizon=1,
        baseline_per_90=6.0,
    )
    assert result.total == pytest.approx(3.0)


def test_an_injured_player_projects_nothing():
    result = pj.project(
        past=regular(), upcoming=[UpcomingMatch(10, 3)], first_gameweek=10, horizon=1,
        baseline_per_90=6.0, status="i", chance_of_playing=0,
    )  # fmt: skip
    assert result.total == 0.0


def test_a_doubtful_player_is_discounted_then_recovers():
    result = pj.project(
        past=regular(),
        upcoming=[UpcomingMatch(10 + w, 3) for w in range(4)],
        first_gameweek=10,
        horizon=4,
        baseline_per_90=6.0,
        status="d",
        chance_of_playing=50,
    )
    assert [g.expected_points for g in result.gameweeks] == pytest.approx([3.0, 4.0, 5.0, 6.0])


def test_a_horizon_of_zero_is_an_empty_projection():
    result = pj.project(
        past=regular(), upcoming=[], first_gameweek=10, horizon=0, baseline_per_90=6.0
    )
    assert (result.gameweeks, result.total) == ((), 0)


def test_projections_are_never_negative():
    # The optimizer's pruning relies on this. A bad enough run drags the blended rate below zero,
    # so it is clamped.
    bad_run = [PastGameweek(90, -3, matches=1)] * 4
    result = pj.project(
        past=bad_run,
        upcoming=[UpcomingMatch(10, 3)],
        first_gameweek=10,
        horizon=1,
        baseline_per_90=3.5,
    )
    assert result.total >= 0

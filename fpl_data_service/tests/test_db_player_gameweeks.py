import pytest

from fpl_data.db import player_gameweeks as pg
from fpl_data.db import sync_log
from fpl_data.fpl.models import EventLive
from tests.helpers import count_rows, load_gameweek

# Saka (player 12) in the saved data, gameweeks 1-5:
#
#   GW  opponent        home  score  min  goals  pts  xG
#   1   Coventry City   yes   3-0    67   1      9    0.64
#   2   Aston Villa     no    1-0    90   1      11   0.63
#   3   Chelsea         yes   2-1    89   0      2    0.23
#   4   Sunderland      no    2-0    90   1      8    1.38
#   5   Brighton        no    0-3    80   0      2    0.34


def test_upsert_gameweek_stats_twice_keeps_one_copy(seeded_store):
    live = load_gameweek(1)
    for _ in range(2):  # re-saving must replace, not duplicate
        pg.upsert_gameweek_stats(seeded_store, 1, live)

    # Every one of the 610 players registered in gameweek 1 had a match.
    assert count_rows(seeded_store, "player_gameweeks") == 610
    assert count_rows(seeded_store, "player_gameweek_matches") == 610
    assert sync_log.last_synced(seeded_store, sync_log.gameweek_stats_dataset(1)) is not None


def test_resaving_replaces_only_that_gameweek(store_with_gameweeks):
    store = store_with_gameweeks
    total = count_rows(store, "player_gameweeks")
    in_gameweek_5 = store.conn.execute(
        "SELECT COUNT(*) FROM player_gameweeks WHERE gameweek = 5"
    ).fetchone()[0]

    # Re-save gameweek 5 as empty (as if FPL had removed everything from it):
    # gameweek 5's rows go, gameweeks 1-4 stay untouched.
    pg.upsert_gameweek_stats(store, 5, EventLive(elements=[]))
    assert count_rows(store, "player_gameweeks") == total - in_gameweek_5


def test_recent_gameweeks(store_with_gameweeks):
    store = store_with_gameweeks
    weeks = pg.recent_gameweeks(store, 12, 3)
    assert [w.gameweek for w in weeks] == [5, 4, 3]  # most recent first

    # Gameweek 5: away at Brighton, lost 0-3, 80 minutes, 2 points.
    gw5 = weeks[0]
    assert (gw5.minutes, gw5.total_points, gw5.expected_goals) == (80, 2, 0.34)
    assert len(gw5.matches) == 1
    match = gw5.matches[0]
    assert (match.opponent_team_name, match.was_home, match.minutes, match.total_points) == (
        "Brighton",
        False,
        80,
        2,
    )
    # FPL's score is home 3 - away 0; Saka was away, so from his side it's 0-3.
    assert (match.team_score, match.opponent_score) == (0, 3)

    # Asking for more than exist returns what there is.
    assert len(pg.recent_gameweeks(store, 12, 10)) == 5


def test_player_gameweek(store_with_gameweeks):
    store = store_with_gameweeks
    # Gameweek 1: at home to Coventry, won 3-0, scored once, 9 points.
    gw1 = pg.player_gameweek(store, 12, 1)
    assert (gw1.goals_scored, gw1.total_points, gw1.minutes) == (1, 9, 67)
    assert [(m.opponent_team_name, m.was_home) for m in gw1.matches] == [("Coventry City", True)]

    # Not played yet: nothing, and not an error.
    assert pg.player_gameweek(store, 12, 30) is None


def test_gameweeks_he_sat_out_are_kept(store_with_gameweeks):
    store = store_with_gameweeks
    # A gameweek where the player didn't get on must be stored, with its match,
    # so "played 3 of the last 5" can be worked out.
    row = store.conn.execute(
        "SELECT player_id, gameweek FROM player_gameweeks WHERE minutes = 0 LIMIT 1"
    ).fetchone()
    week = pg.player_gameweek(store, row["player_id"], row["gameweek"])
    assert len(week.matches) == 1
    assert week.matches[0].minutes == 0


@pytest.mark.parametrize("player_id", [12, 1, 411])
def test_match_points_add_up_to_the_weekly_total(store_with_gameweeks, player_id):
    for week in pg.recent_gameweeks(store_with_gameweeks, player_id, 10):
        assert sum(m.total_points for m in week.matches) == week.total_points

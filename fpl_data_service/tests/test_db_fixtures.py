import pytest

from fpl_data.db import bootstrap, fixtures, sync_log
from tests.helpers import count_rows, load_bootstrap, load_fixtures


def test_upsert_fixtures_twice_keeps_one_copy(store):
    # Teams first: fixtures reference them.
    bootstrap.upsert_bootstrap(store, load_bootstrap())

    for _ in range(2):  # twice: the update path must not duplicate rows
        fixtures.upsert_fixtures(store, load_fixtures())

    assert count_rows(store, "fixtures") == 380
    assert sync_log.last_synced(store, sync_log.FIXTURES) is not None
    # Only the finished fixtures have scores; the rest must be NULL, not 0.
    unplayed_with_score = store.conn.execute(
        "SELECT COUNT(*) FROM fixtures WHERE finished = 0 AND home_score IS NOT NULL"
    ).fetchone()[0]
    assert unplayed_with_score == 0


def test_fixtures_need_teams_and_a_failure_saves_nothing(store):
    # Without teams the foreign keys reject every fixture, and the
    # transaction means nothing at all is saved.
    with pytest.raises(Exception, match="FOREIGN KEY"):
        fixtures.upsert_fixtures(store, load_fixtures())
    assert count_rows(store, "fixtures") == 0
    assert sync_log.last_synced(store, sync_log.FIXTURES) is None


def test_upcoming_fixtures(seeded_store):
    # Arsenal (1) have played gameweeks 1-5 in the saved data.
    upcoming = fixtures.upcoming_fixtures(seeded_store, 1, 3)
    assert [f.id for f in upcoming] == [51, 69, 71]

    # Next up: home to Leeds (13) in gameweek 6, then away at Nott'm Forest (18).
    first = upcoming[0]
    assert (first.gameweek, first.home_team_name, first.away_team_name) == (6, "Arsenal", "Leeds")
    assert (first.home_difficulty, first.away_difficulty) == (3, 5)
    assert first.finished is False
    assert first.home_score is None
    assert upcoming[1].away_team_name == "Arsenal"


def test_upcoming_fixtures_all_remaining_sorted_by_kickoff(seeded_store):
    everything = fixtures.upcoming_fixtures(seeded_store, 1)  # limit 0 = all: 38 - 5 played
    assert len(everything) == 33
    kickoffs = [f.kickoff_time for f in everything]
    assert kickoffs == sorted(kickoffs)


def test_unknown_team_has_no_fixtures(seeded_store):
    assert fixtures.upcoming_fixtures(seeded_store, 99, 5) == []

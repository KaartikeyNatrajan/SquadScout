from datetime import timedelta

import pytest

from fpl_data.db import fixtures as fixtures_db
from fpl_data.db import player_gameweeks, sync_log
from fpl_data.refresh import Refresher, RefreshError


@pytest.fixture
def refresher(store, client):
    return Refresher(store, client, timedelta(hours=1))


async def test_all_downloads_then_skips_while_fresh(refresher, store, fake_fpl):
    await refresher.all()  # first run: nothing stored yet, so everything is downloaded
    await refresher.all()  # second run straight after: it's all fresh, nothing is downloaded

    assert fake_fpl.hits("/bootstrap-static/") == 1
    assert fake_fpl.hits("/fixtures/") == 1
    # And the data really arrived.
    assert len(fixtures_db.upcoming_fixtures(store, 1)) == 33


async def test_all_redownloads_when_stale(store, client, fake_fpl):
    stale_at_once = Refresher(
        store, client, timedelta(0)
    )  # max age 0: everything is stale immediately
    await stale_at_once.all()
    await stale_at_once.all()
    assert fake_fpl.hits("/bootstrap-static/") == 2


async def test_failure_is_reported_and_not_recorded(refresher, store, fake_fpl):
    fake_fpl.down = True

    with pytest.raises(RefreshError, match="bootstrap"):
        await refresher.all()
    # Bootstrap failed, so fixtures (which need its teams) weren't even tried.
    assert fake_fpl.hits("/fixtures/") == 0
    # Nothing was recorded as synced, so the next attempt will try again.
    assert sync_log.last_synced(store, sync_log.BOOTSTRAP) is None


async def test_gameweeks_downloads_each_played_gameweek(refresher, store, fake_fpl):
    await refresher.all()

    # The saved bootstrap is from during gameweek 5: gameweeks 1-5 are played.
    for gameweek in range(1, 6):
        assert fake_fpl.hits(f"/event/{gameweek}/live/") == 1
    assert fake_fpl.hits("/event/6/live/") == 0  # gameweek 6 hasn't started

    # And the stats are queryable: Saka's gameweeks.
    assert len(player_gameweeks.recent_gameweeks(store, 12, 10)) == 5


async def test_only_recent_gameweeks_go_stale(store, client, fake_fpl):
    stale_at_once = Refresher(store, client, timedelta(0))
    await stale_at_once.all()
    await stale_at_once.all()

    # Gameweeks 1-3 are final: downloaded once even though the max age is 0.
    # Gameweeks 4 and 5 (the previous and current) are downloaded each time.
    downloads = {gameweek: fake_fpl.hits(f"/event/{gameweek}/live/") for gameweek in range(1, 6)}
    assert downloads == {1: 1, 2: 1, 3: 1, 4: 2, 5: 2}


async def test_gameweeks_are_retried_after_a_failure(refresher, fake_fpl):
    await refresher.bootstrap()
    await refresher.fixtures()

    fake_fpl.down = True
    with pytest.raises(RefreshError):
        await refresher.gameweeks()
    fake_fpl.down = False
    await refresher.gameweeks()

    # Nothing was recorded during the outage, so the next run downloads
    # everything: gameweek 1 was attempted twice, the rest once.
    assert fake_fpl.hits("/event/1/live/") == 2
    assert fake_fpl.hits("/event/5/live/") == 1

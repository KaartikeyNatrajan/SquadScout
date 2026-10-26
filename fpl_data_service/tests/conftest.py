"""Fixtures shared by every test module."""

import pytest

from fpl_data.db.store import Store
from fpl_data.fpl.client import FplClient
from tests.fake_fpl import FakeFpl


@pytest.fixture
def fake_fpl() -> FakeFpl:
    return FakeFpl()


@pytest.fixture
async def client(fake_fpl: FakeFpl):
    """An FplClient talking to the fake FPL API."""
    client = FplClient("http://fpl.test", transport=fake_fpl.transport)
    yield client
    await client.aclose()


@pytest.fixture
def store(tmp_path):
    """A fresh, empty database in a temporary folder."""
    store = Store.open(tmp_path / "test.db")
    yield store
    store.close()


@pytest.fixture
def seeded_store(store):
    """A store holding the saved bootstrap (players, teams, ...) and fixtures."""
    from fpl_data.db import bootstrap, fixtures
    from tests.helpers import load_bootstrap, load_fixtures

    bootstrap.upsert_bootstrap(store, load_bootstrap())
    fixtures.upsert_fixtures(store, load_fixtures())
    return store


@pytest.fixture
def store_with_gameweeks(seeded_store):
    """A seeded store that also holds every player's stats for gameweeks 1-5."""
    from fpl_data.db import player_gameweeks
    from tests.helpers import load_gameweek

    for gameweek in range(1, 6):
        player_gameweeks.upsert_gameweek_stats(seeded_store, gameweek, load_gameweek(gameweek))
    return seeded_store


@pytest.fixture
async def service(store, client):
    """A service whose store already holds everything the background refresh
    downloads (bootstrap, fixtures, gameweeks 1-5). Managers' teams are
    downloaded on demand, as in production. `fake_fpl` is the fake API behind
    it, for counting requests or taking it down."""
    from datetime import timedelta

    from fpl_data.handlers.service import FplService
    from fpl_data.manager.loader import Loader
    from fpl_data.refresh import Refresher

    await Refresher(store, client, timedelta(hours=1)).all()
    return FplService(store, Loader(client, store))

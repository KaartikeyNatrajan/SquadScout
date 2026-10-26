from datetime import UTC, datetime, timedelta

from fpl_data.db import bootstrap, gameweeks, players, sync_log
from fpl_data.db import store as store_module
from fpl_data.db.store import Store
from tests.helpers import count_rows, load_bootstrap


def test_upsert_bootstrap_twice_keeps_one_copy(store):
    b = load_bootstrap()
    assert sync_log.last_synced(store, sync_log.BOOTSTRAP) is None  # never synced yet

    # Twice: the second run takes the ON CONFLICT ... DO UPDATE path, which
    # must neither fail nor create duplicates.
    bootstrap.upsert_bootstrap(store, b)
    bootstrap.upsert_bootstrap(store, b)

    last = sync_log.last_synced(store, sync_log.BOOTSTRAP)
    assert last is not None
    assert datetime.now(UTC) - last < timedelta(minutes=1)

    expected = {"teams": 20, "events": 38, "chips": 8, "players": 667, "sync_log": 1}
    assert {table: count_rows(store, table) for table in expected} == expected

    # The update path must keep short_name, not overwrite it with name.
    row = store.conn.execute("SELECT short_name FROM teams WHERE id = 1").fetchone()
    assert row["short_name"] == "ARS"


def test_player_fields_are_converted(store):
    bootstrap.upsert_bootstrap(store, load_bootstrap())

    # Saka (12): the string -> float conversions, and JSON null stored as NULL, not 0.
    saka = players.get_player(store, 12)
    assert (saka.goals_scored, saka.expected_goals, saka.now_cost, saka.expected_points_next) == (
        3,
        3.22,
        95,
        5.8,
    )
    assert saka.chance_of_playing_next_round is None
    assert (saka.transfers_in_event, saka.transfers_out_event) == (99910, 44545)

    # White (10) is doubtful: status, news and a 75% chance.
    white = players.get_player(store, 10)
    assert white.status == "d"
    assert white.news == "Groin injury - 75% chance of playing"
    assert white.chance_of_playing_next_round == 75


def test_gameweeks_and_deadlines(store):
    # Before any download there are no gameweeks at all.
    assert gameweeks.next_gameweek(store) is None
    assert gameweeks.current_gameweek(store) is None

    bootstrap.upsert_bootstrap(store, load_bootstrap())

    # The saved bootstrap is from during gameweek 5.
    assert gameweeks.current_gameweek(store) == 5
    assert gameweeks.next_gameweek(store) == gameweeks.GameweekRow(6, "2026-10-10T10:00:00Z")
    assert gameweeks.deadline(store, 6) == "2026-10-10T10:00:00Z"
    assert gameweeks.deadline(store, 39) is None


def test_chips(store):
    bootstrap.upsert_bootstrap(store, load_bootstrap())

    chips = gameweeks.chips(store)
    # Two of each chip: one for gameweeks 1/2-19, one for 20-38.
    assert len(chips) == 8
    assert sorted(c.name for c in chips).count("wildcard") == 2


def test_open_resets_an_outdated_schema(tmp_path):
    path = tmp_path / "test.db"
    store = Store.open(path)
    bootstrap.upsert_bootstrap(store, load_bootstrap())
    # Pretend the database was made by an older version of the code.
    store.conn.execute("PRAGMA user_version = 1")
    store.close()

    store = Store.open(path)
    assert count_rows(store, "players") == 0
    assert store.conn.execute("PRAGMA user_version").fetchone()[0] == store_module.SCHEMA_VERSION
    store.close()


def test_open_keeps_a_current_schema(tmp_path):
    path = tmp_path / "test.db"
    store = Store.open(path)
    bootstrap.upsert_bootstrap(store, load_bootstrap())
    store.close()

    store = Store.open(path)
    assert count_rows(store, "players") == 667
    store.close()


def test_failed_transaction_saves_nothing(store):
    try:
        with store.transaction() as conn:
            conn.execute("INSERT INTO teams (id, name, short_name) VALUES (1, 'A', 'A')")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert count_rows(store, "teams") == 0

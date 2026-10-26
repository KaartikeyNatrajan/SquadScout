from datetime import UTC, datetime, timedelta

import pytest

from fpl_data.db import bootstrap, players, transfer_snapshots
from tests.helpers import count_rows, load_bootstrap

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def with_extra_transfers(b, extra: dict[int, tuple[int, int]]):
    """A copy of the bootstrap in which some players have had more transfers (in, out) since."""
    b = b.model_copy(deep=True)
    for p in b.elements:
        if p.id in extra:
            p.transfers_in += extra[p.id][0]
            p.transfers_out += extra[p.id][1]
    return b


def test_net_transfers_and_ranks_are_stored(store):
    bootstrap.upsert_bootstrap(store, load_bootstrap())

    # In the saved data Groß was bought by the most managers, and Saka's sales are high.
    gross = players.get_player(store, 124)
    assert (gross.net_bought_rank, gross.net_sold_rank) == (1, 667)
    # 763,845 managers bought him and 10,818 sold him: a net 753,027, of 10,991,103 managers.
    assert gross.transfers_in_event - gross.transfers_out_event == 753_027
    assert gross.net_transfers_percent == pytest.approx(100 * 753_027 / 10_991_103)

    all_players = players.find_players(
        store, players.PlayerFilter(include_unavailable=True, limit=1000)
    )
    assert sorted(p.net_bought_rank for p in all_players) == list(
        range(1, 668)
    )  # every rank used once
    assert sorted(p.net_sold_rank for p in all_players) == list(range(1, 668))


def test_price_change_progress_and_projections_are_stored(store):
    bootstrap.upsert_bootstrap(store, load_bootstrap())

    gross = players.get_player(store, 124)
    assert gross.price_change_progress == 86.0
    assert gross.price_projections == [86.7, 87.6, 88.5]  # in the order of FPL's offsets 0, 1, 2


def test_a_snapshot_is_saved_with_every_refresh_and_old_ones_are_deleted(store):
    b = load_bootstrap()
    bootstrap.upsert_bootstrap(store, b, now=NOW - timedelta(days=9))
    bootstrap.upsert_bootstrap(store, b, now=NOW - timedelta(days=2))
    assert count_rows(store, "transfer_snapshots") == 2 * 667

    bootstrap.upsert_bootstrap(store, b, now=NOW)  # deletes anything older than 8 days

    taken = {r[0] for r in store.conn.execute("SELECT DISTINCT taken_at FROM transfer_snapshots")}
    assert taken == {"2026-10-18T12:00:00Z", "2026-10-20T12:00:00Z"}


def test_two_refreshes_in_the_same_second_keep_one_snapshot(store):
    b = load_bootstrap()
    bootstrap.upsert_bootstrap(store, b, now=NOW)
    bootstrap.upsert_bootstrap(store, b, now=NOW)
    assert count_rows(store, "transfer_snapshots") == 667


def test_net_transfers_over_the_last_24_hours(store):
    b = load_bootstrap()
    bootstrap.upsert_bootstrap(store, b, now=NOW - timedelta(hours=25))
    # A day later: Saka's bought by 1,000 more managers and sold by 400 more; Groß's bought by 5,000 more.
    bootstrap.upsert_bootstrap(
        store, with_extra_transfers(b, {12: (1000, 400), 124: (5000, 0), 411: (0, 300)}), now=NOW
    )

    net = transfer_snapshots.net_transfers_over(store, now=NOW)

    assert net[12] == 600
    assert net[124] == 5000
    assert net[411] == -300
    assert net[10] == 0  # everyone else is still there, with no change
    assert len(net) == 667


def test_the_24_hour_figure_works_across_a_deadline(store):
    """The per-gameweek counts restart at every deadline; the season totals don't."""
    b = load_bootstrap()
    bootstrap.upsert_bootstrap(store, b, now=NOW - timedelta(hours=26))
    after_deadline = with_extra_transfers(b, {12: (7000, 1000)})
    for p in after_deadline.elements:
        p.transfers_in_event = p.transfers_out_event = (
            0  # the deadline passed: the weekly counts restart
        )
    bootstrap.upsert_bootstrap(store, after_deadline, now=NOW)

    assert transfer_snapshots.net_transfers_over(store, now=NOW)[12] == 6000


def test_no_24_hour_figure_until_there_is_a_snapshot_that_old(store):
    bootstrap.upsert_bootstrap(store, load_bootstrap(), now=NOW - timedelta(hours=3))
    assert transfer_snapshots.net_transfers_over(store, now=NOW) == {}


def test_no_24_hour_figure_if_the_only_old_snapshot_is_far_too_old(store):
    # The service was off for two days: a 3-day-old snapshot says nothing about "the last 24 hours".
    bootstrap.upsert_bootstrap(store, load_bootstrap(), now=NOW - timedelta(days=3))
    bootstrap.upsert_bootstrap(store, load_bootstrap(), now=NOW - timedelta(hours=1))
    assert transfer_snapshots.net_transfers_over(store, now=NOW) == {}

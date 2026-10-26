from datetime import UTC, datetime, timedelta

import pytest

from fpl_data.fpl.client import FplError
from fpl_data.manager.loader import Loader, NoSquadYetError, TeamNotFoundError
from fpl_data.refresh import Refresher

# A moment before the saved data's next deadline (gameweek 6, 2026-10-10 10:00
# UTC), and one after it.
BEFORE_DEADLINE = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
AFTER_DEADLINE = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


@pytest.fixture
async def loader(store, client):
    """A Loader whose store holds the saved bootstrap (players, chip rules,
    deadlines) and whose clock is set to BEFORE_DEADLINE."""
    await Refresher(store, client, timedelta(hours=1)).bootstrap()
    return Loader(client, store, now=lambda: BEFORE_DEADLINE)


def pick_at(team, position):
    return next(p for p in team.picks if p.squad_position == position)


async def test_a_normal_team(loader):
    # Team 6 had a normal gameweek 5: two transfers, no chip.
    team = await loader.load(6)

    assert (team.squad_gameweek, team.next_gameweek, len(team.picks)) == (5, 6, 15)
    assert (team.bank, team.free_transfers) == (1, 1)  # £0.1m in the bank
    assert team.chips_available == ["freehit"]

    # Haaland (position 11): captain, bought at £15.5m, now £15.6m: sells for £15.5m.
    haaland = pick_at(team, 11)
    assert haaland.is_captain
    assert (haaland.selling_price, haaland.selling_price_is_estimate) == (155, False)
    # Raya (position 1) has been in the squad from the start. He cost £6.0m in
    # gameweek 1 and is £6.1m now: the 0.1 rise is split, and half of 0.1 rounds
    # down, so he sells for £6.0m. Not an estimate: it comes from his price history.
    raya = pick_at(team, 1)
    assert (raya.selling_price, raya.selling_price_is_estimate) == (60, False)
    assert any("priced as they cost in gameweek 1" in note for note in team.notes)


async def test_after_a_free_hit_the_previous_squad_is_shown(loader):
    # Team 1 played a Free Hit in gameweek 5, so its real squad is gameweek 4's.
    team = await loader.load(1)

    assert (team.squad_gameweek, team.next_gameweek) == (4, 6)
    # The gameweek 4 captain was Palmer; the Free Hit captain was Haaland.
    assert pick_at(team, 6).is_captain
    # 1 after GW1, +1 for GW2 and GW4; the Wildcard (GW3) and Free Hit (GW5)
    # weeks add none: 1 -> 2 -> 2 -> 3 -> 3.
    assert team.free_transfers == 3
    assert any("Free Hit" in note for note in team.notes)


async def test_unknown_team(loader):
    with pytest.raises(TeamNotFoundError):
        await loader.load(999)


async def test_no_squad_before_the_first_deadline(loader, fake_fpl, monkeypatch):
    from fpl_data.fpl import models

    async def new_team(team_id):
        return models.Entry(
            id=team_id,
            name="New",
            player_first_name="A",
            player_last_name="B",
            started_event=6,
            current_event=None,
            summary_overall_points=0,
            summary_overall_rank=None,
        )

    monkeypatch.setattr(loader.client, "fetch_entry", new_team)
    with pytest.raises(NoSquadYetError):
        await loader.load(5)


async def test_saved_until_the_deadline(loader, fake_fpl):
    for _ in range(3):
        await loader.load(6)
    assert fake_fpl.hits("/entry/6/") == 1  # before the deadline: 1 download for 3 loads

    # Once the deadline has passed, the squad may have changed: download again.
    loader.now = lambda: AFTER_DEADLINE
    await loader.load(6)
    assert fake_fpl.hits("/entry/6/") == 2


async def test_saved_team_survives_a_restart(loader, store, client, fake_fpl):
    await loader.load(6)

    # A new Loader on the same database, as after restarting the server.
    restarted = Loader(client, store, now=lambda: BEFORE_DEADLINE)
    team = await restarted.load(6)

    assert fake_fpl.hits("/entry/6/") == 1  # the saved copy: 1 download in total
    # The saved copy is the same team, captain and all.
    assert len(team.picks) == 15
    assert pick_at(team, 11).is_captain
    assert team.free_transfers == 1


async def test_api_down_serves_the_saved_copy(loader, fake_fpl):
    await loader.load(6)

    # The deadline passes and FPL goes down: the old copy, with a warning.
    loader.now = lambda: AFTER_DEADLINE
    fake_fpl.down = True
    team = await loader.load(6)

    assert any("couldn't be reached" in note for note in team.notes)


async def test_api_down_with_nothing_saved_is_an_error(loader, fake_fpl):
    fake_fpl.down = True
    with pytest.raises(FplError):  # the handler maps this to UNAVAILABLE
        await loader.load(6)


async def test_a_first_request_fetches_picks_history_and_transfers_together(
    loader, client, monkeypatch
):
    """They don't depend on each other, so they must be in flight at the same time."""
    import asyncio

    in_flight = peak = 0
    for name in ("fetch_entry_picks", "fetch_entry_history", "fetch_entry_transfers"):
        original = getattr(client, name)

        async def slowed(*args, _original=original, **kwargs):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)  # long enough for the others to start
            try:
                return await _original(*args, **kwargs)
            finally:
                in_flight -= 1

        monkeypatch.setattr(client, name, slowed)

    await loader.load(6)

    assert peak == 3


async def test_original_players_prices_come_from_their_history(loader, fake_fpl):
    team = await loader.load(6)

    # Raya (1), Calafiori (8) and João Pedro (165) were never bought by a transfer.
    for player_id in (1, 8, 165):
        assert fake_fpl.hits(f"/element-summary/{player_id}/") == 1
    assert [p.selling_price_is_estimate for p in team.picks] == [False] * 15
    # Calafiori: £5.5m in gameweek 1, £5.8m in the saved bootstrap -> 5.5 + half of 0.3 = £5.6m.
    calafiori = next(p for p in team.picks if p.player_id == 8)
    assert calafiori.selling_price == 56


async def test_price_histories_are_downloaded_once_and_kept(loader, fake_fpl):
    await loader.load(6)

    # After the deadline the team is downloaded again, but a past price never changes.
    loader.now = lambda: AFTER_DEADLINE
    await loader.load(6)

    assert fake_fpl.hits("/entry/6/") == 2
    assert fake_fpl.hits("/element-summary/1/") == 1


async def test_an_unavailable_price_history_falls_back_to_an_estimate(loader, client, monkeypatch):
    async def failing(player_id):
        raise FplError("price history unavailable")

    monkeypatch.setattr(client, "fetch_element_summary", failing)

    team = await loader.load(6)

    raya = pick_at(team, 1)
    assert (raya.selling_price, raya.selling_price_is_estimate) == (
        61,
        True,
    )  # current price, flagged
    assert any("could not be found" in note for note in team.notes)

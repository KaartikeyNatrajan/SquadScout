import pytest

from fpl_data.db.gameweeks import ChipRow
from fpl_data.fpl.models import ChipPlay, EntryGameweek, EntryTransfer
from fpl_data.manager import rules


def week(event: int, transfers: int = 0, cost: int = 0) -> EntryGameweek:
    return EntryGameweek(event=event, event_transfers=transfers, event_transfers_cost=cost)


@pytest.mark.parametrize(
    ("started", "last", "history", "chips", "expected"),
    [
        pytest.param(5, 5, [], [], 1, id="just joined"),
        pytest.param(1, 2, [week(2)], [], 2, id="saved one"),
        pytest.param(1, 2, [week(2, 1)], [], 1, id="used one"),
        pytest.param(1, 10, [], [], 5, id="capped at 5"),
        pytest.param(1, 3, [week(2), week(3, 3, 4)], [], 1, id="took a hit"),
        # A recorded hit corrects the estimate: whatever we thought, all the
        # free transfers were used.
        pytest.param(1, 4, [week(4, 2, 4)], [], 1, id="a hit resyncs"),
        pytest.param(
            1,
            3,
            [week(2)],
            [ChipPlay(name="wildcard", event=3)],
            2,
            id="a chip week adds no free transfer",
        ),
        pytest.param(
            1,
            4,
            [week(2), week(4)],
            [ChipPlay(name="freehit", event=3)],
            3,
            id="saved ones survive a chip",
        ),
        # Team 6 in the saved data: nothing in GW2, Wildcard in GW3, 1 transfer
        # in GW4, 2 in GW5: 1 -> 2 -> 2 -> 2 -> 1.
        pytest.param(
            1,
            5,
            [week(2), week(3), week(4, 1), week(5, 2)],
            [
                ChipPlay(name="bboost", event=1),
                ChipPlay(name="wildcard", event=3),
                ChipPlay(name="3xc", event=4),
            ],
            1,
            id="team 6",
        ),
    ],
)
def test_free_transfers(started, last, history, chips, expected):
    assert rules.free_transfers(started, last, history, chips) == expected


def test_chips_available():
    # The real rules: each chip once in gameweeks 1/2-19 and once in 20-38.
    allowances = [
        ChipRow("wildcard", 2, 19),
        ChipRow("wildcard", 20, 38),
        ChipRow("freehit", 2, 19),
        ChipRow("bboost", 1, 19),
        ChipRow("3xc", 1, 19),
        ChipRow("freehit", 20, 38),
        ChipRow("bboost", 20, 38),
        ChipRow("3xc", 20, 38),
    ]
    played = [
        ChipPlay(name="bboost", event=1),
        ChipPlay(name="wildcard", event=3),
        ChipPlay(name="3xc", event=4),
    ]

    # Gameweek 6: only the first-half Free Hit is left.
    assert rules.chips_available(allowances, played, 6) == ["freehit"]
    # Gameweek 20: the second half brings a fresh set of all four.
    assert len(rules.chips_available(allowances, played, 20)) == 4


def test_purchase_prices():
    transfers = [
        EntryTransfer(
            element_in=10, element_in_cost=50, element_out=1, element_out_cost=1, event=3
        ),
        EntryTransfer(
            element_in=10, element_in_cost=55, element_out=1, element_out_cost=1, event=7
        ),  # bought again: this counts
        EntryTransfer(
            element_in=20, element_in_cost=80, element_out=1, element_out_cost=1, event=5
        ),  # a Free Hit purchase: ignored
    ]
    prices = rules.purchase_prices(transfers, {5})

    assert prices[10] == 55
    assert 20 not in prices


@pytest.mark.parametrize(
    ("purchase", "now", "expected"),
    [
        (50, 50, 50),  # unchanged
        (50, 48, 48),  # fell: sells at the current price
        (50, 52, 51),  # rose 0.2: keeps half
        (50, 53, 51),  # rose 0.3: half is 0.15, rounded down to 0.1
        (155, 156, 155),
    ],
)
def test_selling_price(purchase, now, expected):
    assert rules.selling_price(purchase, now) == expected

import pytest

from fpl_data import sentiment


@pytest.mark.parametrize(
    ("net", "percent", "bought_rank", "sold_rank", "expected"),
    [
        # Heavily bought: 0.5% of all managers or more.
        (
            351_764,
            3.15,
            1,
            600,
            "heavily bought: net +3.15% of all managers this gameweek (the most bought player)",
        ),
        (
            90_565,
            0.81,
            5,
            600,
            "heavily bought: net +0.81% of all managers this gameweek (the 5th most bought player)",
        ),
        # Being bought: between 0.1% and 0.5%.
        (
            20_000,
            0.18,
            22,
            640,
            "being bought: net +0.18% of all managers this gameweek (the 22nd most bought player)",
        ),
        # Sold, with the sign kept so it reads naturally.
        (
            -177_748,
            -1.59,
            660,
            1,
            "heavily sold: net -1.59% of all managers this gameweek (the most sold player)",
        ),
        (
            -15_000,
            -0.13,
            640,
            23,
            "being sold: net -0.13% of all managers this gameweek (the 23rd most sold player)",
        ),
        # Not enough movement to mean anything.
        (500, 0.004, 100, 500, "little transfer activity this gameweek"),
        (-500, -0.004, 500, 100, "little transfer activity this gameweek"),
        (0, 0.0, 300, 300, "little transfer activity this gameweek"),
        # The thresholds themselves.
        (
            1,
            sentiment.MODERATE_PERCENT,
            50,
            50,
            "being bought: net +0.10% of all managers this gameweek (the 50th most bought player)",
        ),
        (
            1,
            sentiment.HEAVY_PERCENT,
            9,
            50,
            "heavily bought: net +0.50% of all managers this gameweek (the 9th most bought player)",
        ),
    ],
)
def test_transfer_sentiment(net, percent, bought_rank, sold_rank, expected):
    assert sentiment.transfer_sentiment(net, percent, bought_rank, sold_rank) == expected


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        (1, "1st"),
        (2, "2nd"),
        (3, "3rd"),
        (4, "4th"),
        (11, "11th"),
        (12, "12th"),
        (13, "13th"),
        (21, "21st"),
        (22, "22nd"),
        (101, "101st"),
        (111, "111th"),
    ],
)
def test_ordinals(number, expected):
    assert sentiment._ordinal(number) == expected


@pytest.mark.parametrize(
    ("progress", "projections", "expected"),
    [
        # FPL's projection reaches +100 at the next update: a rise is imminent.
        (88.0, [101.5, 120.0, 130.0], "likely to rise at the next price update"),
        (72.1, [88.4, 107.7, 126.9], "likely to rise within 2 price updates"),
        (64.0, [77.8, 93.8, 109.8], "likely to rise within 3 price updates"),
        (-80.0, [-100.5, -110.0, -120.0], "likely to fall at the next price update"),
        (-60.9, [-75.0, -101.0, -110.0], "likely to fall within 2 price updates"),
        # Moving, but not that far.
        (
            34.0,
            [40.0, 46.0, 52.0],
            "no price change expected within 3 price updates (34% of the way to a rise)",
        ),
        (
            -21.5,
            [-24.5, -28.0, -31.5],
            "no price change expected within 3 price updates (22% of the way to a fall)",
        ),
        # Hardly moving at all.
        (1.2, [1.2, 1.3, 1.3], "price stable: no change expected soon"),
        (-0.0, [], "price stable: no change expected soon"),
    ],
)
def test_price_outlook(progress, projections, expected):
    assert sentiment.price_outlook(progress, projections) == expected

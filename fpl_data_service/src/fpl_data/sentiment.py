"""Plain-word summaries of what other FPL managers are doing with a player.

Transfers in and out show what the crowd thinks of a player: heavy buying means
form, a good run of fixtures or a tip-off; heavy selling usually means injury,
a bad run, or losing his place. The raw numbers (hundreds of thousands of
transfers) mean little to a small language model, so these functions turn them
into a sentence. Pure functions, like squad.py.
"""

from collections.abc import Sequence

# A player counts as "heavily" bought or sold when this share of ALL FPL managers
# (in percent) moved him in one gameweek. On real data that is about the top 2.5%
# of players (17 of ~670); MODERATE is about the top 10% (65 of ~670).
HEAVY_PERCENT = 0.5
MODERATE_PERCENT = 0.1

# FPL updates prices roughly once a day; its projections look this many updates ahead.
_WHEN = ["at the next price update", "within 2 price updates", "within 3 price updates"]


def transfer_sentiment(net: int, percent: float, bought_rank: int, sold_rank: int) -> str:
    """What managers are doing with the player this gameweek.

    `net` is transfers in minus out since the last deadline, `percent` that as a
    share of all managers, and the ranks say where he stands among all players
    (1 = the most bought, or the most sold).
    """
    if net > 0 and percent >= MODERATE_PERCENT:
        kind = "heavily bought" if percent >= HEAVY_PERCENT else "being bought"
        return f"{kind}: net +{percent:.2f}% of all managers this gameweek ({_standing(bought_rank, 'bought')})"
    if net < 0 and -percent >= MODERATE_PERCENT:
        kind = "heavily sold" if -percent >= HEAVY_PERCENT else "being sold"
        return f"{kind}: net {percent:.2f}% of all managers this gameweek ({_standing(sold_rank, 'sold')})"
    return "little transfer activity this gameweek"


def price_outlook(progress: float, projections: Sequence[float]) -> str:
    """Whether the player's price is about to move.

    `progress` runs from -100 (a fall) to +100 (a rise) and `projections` is
    that progress after the next 1, 2 and 3 price updates, as FPL projects it.
    FPL doesn't document these numbers; this is how they behave: the sign
    follows net transfers, and a change happens when they reach +-100.
    """
    for updates_ahead, value in enumerate(projections):
        if value >= 100:
            return f"likely to rise {_WHEN[updates_ahead]}"
        if value <= -100:
            return f"likely to fall {_WHEN[updates_ahead]}"
    if abs(progress) < 10:
        return "price stable: no change expected soon"
    direction = "a rise" if progress > 0 else "a fall"
    return f"no price change expected within 3 price updates ({abs(progress):.0f}% of the way to {direction})"


def _standing(rank: int, verb: str) -> str:
    """ "the most bought player", "the 3rd most bought player"."""
    if rank <= 1:
        return f"the most {verb} player"
    return f"the {_ordinal(rank)} most {verb} player"


def _ordinal(n: int) -> str:
    """1 -> "1st", 2 -> "2nd", 11 -> "11th", 23 -> "23rd"."""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"

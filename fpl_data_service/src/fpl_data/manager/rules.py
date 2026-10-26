"""FPL rules that the API doesn't apply for us.

Each function works only on the values passed in (no network, no database), so
each is easy to test.
"""

from collections.abc import Collection

from fpl_data import squad
from fpl_data.db.gameweeks import ChipRow
from fpl_data.fpl.models import ChipPlay, EntryGameweek, EntryTransfer


def free_transfers(
    started_event: int, last_event: int, history: list[EntryGameweek], chips: list[ChipPlay]
) -> int:
    """Estimate how many free transfers a team has for the gameweek after `last_event`.

    FPL doesn't publish this, so it's rebuilt from the team's history with the
    2024/25-onwards rules:

    - After the team's first deadline it has 1 free transfer.
    - Each gameweek, unused free transfers carry over, plus 1 more, up to a
      maximum of 5.
    - Transfers made for a gameweek use up its free transfers first; extra ones
      cost points (a "hit") and leave 0 to carry over.
    - In a Wildcard or Free Hit gameweek the saved free transfers are kept,
      but no new one is added for it.

    The chip rule was checked against the recorded points hits of 61 real teams
    that had played one of those chips (343 gameweeks): this rule predicted
    every one. The obvious alternative, adding the usual +1 in a chip week, got
    6 of them wrong.
    """
    by_event = {h.event: h for h in history}
    chip_in = {c.event: c.name for c in chips}

    free = 1  # available for the gameweek after the first one
    for gameweek in range(started_event + 1, last_event + 1):
        if chip_in.get(gameweek) in (squad.CHIP_WILDCARD, squad.CHIP_FREE_HIT):
            continue  # saved free transfers are kept, none used up, none added
        played = by_event.get(gameweek, EntryGameweek(event=gameweek))
        free -= played.event_transfers
        # A hit means more transfers were made than were free, so none carry
        # over. Trusting the recorded cost also corrects the estimate if it
        # had drifted.
        if free < 0 or played.event_transfers_cost > 0:
            free = 0
        free = min(free + 1, squad.MAX_FREE_TRANSFERS)
    return free


def chips_available(rules: list[ChipRow], played: list[ChipPlay], gameweek: int) -> list[str]:
    """The chips a team can still play in `gameweek`, e.g. ["freehit", "3xc"].

    Each allowance in `rules` may be played once within its window; a play
    inside a window uses that window's allowance.
    """
    available: list[str] = []
    for rule in rules:
        if not rule.start_event <= gameweek <= rule.stop_event or rule.name in available:
            continue
        used = any(
            p.name == rule.name and rule.start_event <= p.event <= rule.stop_event for p in played
        )
        if not used:
            available.append(rule.name)
    return available


def purchase_prices(
    transfers: list[EntryTransfer], free_hit_events: Collection[int]
) -> dict[int, int]:
    """What the team paid for each player it bought, keyed by player id, using
    his most recent purchase.

    Free Hit gameweeks are skipped: their transfers are undone when the squad
    returns to normal. Players in the squad since the team was created have no
    entry, because the API doesn't record their price.
    """
    prices: dict[int, int] = {}
    latest: dict[int, int] = {}  # player id -> gameweek of the purchase in `prices`
    for t in transfers:
        if t.event in free_hit_events:
            continue
        if t.event >= latest.get(t.element_in, 0):
            latest[t.element_in] = t.event
            prices[t.element_in] = t.element_in_cost
    return prices


def selling_price(purchase: int, now: int) -> int:
    """What FPL pays when a player is sold: his current price if it hasn't risen
    since he was bought, otherwise his purchase price plus half of the rise,
    rounded down to the nearest £0.1m. All in tenths of £m."""
    if now <= purchase:
        return now
    return purchase + (now - purchase) // 2

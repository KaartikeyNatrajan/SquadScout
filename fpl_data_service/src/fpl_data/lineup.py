"""The best starting eleven a squad can field, by projected points.

A squad has 15 players but only 11 score, so a transfer is worth the change in
the points of the eleven you would actually pick, not in the sum of the
fifteen. Swapping a bench player for a better one adds nothing unless the new
player is good enough to start.

FPL's rules for the eleven: 1 goalkeeper, at least 3 defenders, at least 2
midfielders and at least 1 forward. With a squad of 2/5/5/3 that leaves these
formations (defenders-midfielders-forwards):
3-4-3, 3-5-2, 4-3-3, 4-4-2, 4-5-1, 5-2-3, 5-3-2, 5-4-1.

Pure arithmetic on numbers, like squad.py.
"""

from collections.abc import Iterable, Mapping

from fpl_data.squad import Member

# Every formation as (defenders, midfielders, forwards), summing to the 10 outfield players.
FORMATIONS = [
    (d, m, f) for d in range(3, 6) for m in range(2, 6) for f in range(1, 4) if d + m + f == 10
]

GOALKEEPER, DEFENDER, MIDFIELDER, FORWARD = 1, 2, 3, 4


def best_eleven(points_by_position: Mapping[int, Iterable[float]]) -> float:
    """The most points a starting eleven can have, given each position's players' points.

    `points_by_position` maps FPL's position number (1 GK .. 4 FWD) to the
    points of the squad's players in that position. Returns -infinity if no
    legal eleven can be made.
    """
    ranked = {
        position: sorted(points_by_position.get(position, ()), reverse=True)
        for position in (GOALKEEPER, DEFENDER, MIDFIELDER, FORWARD)
    }
    if not ranked[GOALKEEPER]:
        return float("-inf")
    goalkeeper = ranked[GOALKEEPER][0]
    # sums[position][n] = the points of that position's n best players.
    sums = {position: _running_totals(points) for position, points in ranked.items()}

    best = float("-inf")
    for defenders, midfielders, forwards in FORMATIONS:
        if (
            len(ranked[DEFENDER]) >= defenders
            and len(ranked[MIDFIELDER]) >= midfielders
            and len(ranked[FORWARD]) >= forwards
        ):
            total = (
                goalkeeper
                + sums[DEFENDER][defenders]
                + sums[MIDFIELDER][midfielders]
                + sums[FORWARD][forwards]
            )
            best = max(best, total)
    return best


def squad_points(members: Iterable[Member], points: Mapping[int, float]) -> float:
    """The best eleven's points for a squad, where `points` maps player id to his points."""
    by_position: dict[int, list[float]] = {}
    for member in members:
        by_position.setdefault(member.position, []).append(points.get(member.player_id, 0.0))
    return best_eleven(by_position)


def _running_totals(values: list[float]) -> list[float]:
    """totals[n] = the sum of the first n values."""
    totals = [0.0]
    for value in values:
        totals.append(totals[-1] + value)
    return totals

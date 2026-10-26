"""Transfer search: finds the best transfers for a squad.

Given a squad, some candidate players and each player's projected points, it
tries every way of swapping 1 to 3 players for same-position candidates and
returns the sets that gain the most points after paying for the hit:

    gain = best starting eleven after the transfers
         - best starting eleven before them
         - the points hit (4 per transfer beyond the free ones)

"Best starting eleven" is lineup.py: only 11 of the 15 players score, so
upgrading someone who would sit on the bench gains nothing, while a new player
good enough to start gains the difference to whoever he displaces.

Every returned set has been checked by squad.apply, the same verifier
EvaluateTransfers uses, so it obeys all the squad rules (15 players, the
position mix, at most 3 per club, within budget). The optimizer has no copy of
the rules.

How it stays fast:

- Candidates are pruned first. A player who costs at least as much as another
  candidate in his position but is projected fewer points can never be a better
  choice, so he is dropped (the few best scorers are always kept, as
  alternatives when the club limit rules out the others).
- A swap needs a replacement in the same position, so the sets of players
  to sell are enumerated first and the replacements are chosen per position.
- Before trying replacements for a given set of sellers, an upper bound on the
  possible gain is computed (assume the best candidates were free and legal). If
  even that can't beat the current worst result, the set is skipped.

Projected points are never negative, which the second pruning step relies on.

Like squad.py, this module is pure: no database, no network, so it is easy to
test and safe to run in a worker thread.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from itertools import combinations, product

from fpl_data import lineup, squad
from fpl_data.squad import Member, Squad

MAX_TRANSFERS = 3  # more than this is rarely wise and the search grows quickly
KEEP_TOP_PER_POSITION = 4  # candidates always kept by the pruning, however pricey
POOL_PER_POSITION = 15  # candidates kept per position at most
DEFAULT_LIMIT = 5  # suggestions returned


@dataclass
class Candidate:
    """A player who could be transferred in."""

    member: Member  # price = what he costs to buy
    expected_points: float  # projected, over whatever horizon the caller chose


@dataclass
class Request:
    """Everything the search needs. Money is in tenths of £m."""

    squad: Squad  # the current squad, valued at selling prices, with the bank
    free_transfers: int
    max_transfers: int  # the most transfers to consider (at most MAX_TRANSFERS)
    max_hit: int | None = None  # None = no limit on points deducted
    expected_points: dict[int, float] = field(
        default_factory=dict
    )  # of each squad member, by player id
    candidates: list[Candidate] = field(default_factory=list)  # the players that may be brought in


@dataclass
class Suggestion:
    """One legal set of transfers."""

    out_ids: list[int]
    in_ids: list[int]
    hit: int
    expected_gain: float  # change in the best eleven's projected points, minus the hit
    # Tie-breaker: the change in the points of all 15 players, bench included. Of two
    # transfers that gain the same, prefer the one that leaves the stronger squad.
    depth_change: float = 0.0


def suggest(request: Request, limit: int = DEFAULT_LIMIT) -> list[Suggestion]:
    """The best legal transfer sets for `request` that gain points, best first.
    An empty list means no transfer is worth making."""
    pool = _prune(request.candidates)
    members = request.squad.members
    points = [request.expected_points.get(m.player_id, 0.0) for m in members]
    before = _eleven([(m.position, p) for m, p in zip(members, points, strict=True)])

    found: list[Suggestion] = []
    for count in range(1, min(request.max_transfers, MAX_TRANSFERS) + 1):
        hit = squad.hit_cost(count, request.free_transfers)
        if request.max_hit is not None and hit > request.max_hit:
            break  # more transfers only cost more

        for sold_at in combinations(range(len(members)), count):
            sold = [members[i] for i in sold_at]
            needed = Counter(m.position for m in sold)
            if any(len(pool.get(position, ())) < n for position, n in needed.items()):
                continue  # not enough candidates to replace them

            # What stays in the squad, as (position, points) pairs.
            stay = [
                (m.position, p)
                for i, (m, p) in enumerate(zip(members, points, strict=True))
                if i not in sold_at
            ]
            # The best case: the best candidates (pool lists are best first), ignoring money and clubs.
            best_case = [
                (position, c.expected_points)
                for position, n in needed.items()
                for c in pool[position][:n]
            ]
            if _eleven(stay + best_case) - before - hit <= _to_beat(found, limit):
                continue  # even the best case can't make the list

            budget = request.squad.bank + sum(m.price for m in sold)
            out_ids = [m.player_id for m in sold]
            for bought in _replacements(pool, needed):
                # Cheap checks first. The eleven can gain at most the points of the
                # new players, and the new players have to be affordable.
                if sum(c.expected_points for c in bought) - hit <= _to_beat(found, limit):
                    continue
                if sum(c.member.price for c in bought) > budget:
                    continue
                gain = (
                    _eleven(stay + [(c.member.position, c.expected_points) for c in bought])
                    - before
                    - hit
                )
                if gain <= _to_beat(found, limit):
                    continue
                members_in = [c.member for c in bought]
                _, problems = squad.apply(request.squad, out_ids, members_in)
                if problems:
                    continue
                depth = sum(c.expected_points for c in bought) - sum(points[i] for i in sold_at)
                found.append(
                    Suggestion(out_ids, [m.player_id for m in members_in], hit, gain, depth)
                )
                found.sort(key=_order)
                del found[limit:]
    return found


def _eleven(players: Iterable[tuple[int, float]]) -> float:
    """The best starting eleven's points, for a squad given as (position, points) pairs."""
    by_position: dict[int, list[float]] = {}
    for position, points in players:
        by_position.setdefault(position, []).append(points)
    return lineup.best_eleven(by_position)


def _to_beat(found: Sequence[Suggestion], limit: int) -> float:
    """The gain a new result must exceed to be worth keeping: more than nothing
    until the list is full, then more than the worst one in it."""
    return found[-1].expected_gain if len(found) >= limit else 0.0


def _order(s: Suggestion) -> tuple:
    """Best first. Ties (to 9 decimal places, to ignore floating point noise) are broken by
    the stronger resulting squad, then by player ids, so results are always in the same order."""
    return (-round(s.expected_gain, 9), -round(s.depth_change, 9), s.hit, s.out_ids, s.in_ids)


def _replacements(pool: dict[int, list[Candidate]], needed: Counter) -> Sequence[list[Candidate]]:
    """Every way to pick the candidates to bring in: for each position, any
    `n` of that position's candidates, `n` being how many players of that
    position are leaving."""
    per_position = [list(combinations(pool[position], n)) for position, n in sorted(needed.items())]
    return [[c for part in parts for c in part] for parts in product(*per_position)]


def _prune(candidates: Sequence[Candidate]) -> dict[int, list[Candidate]]:
    """Per position, drop candidates that are never the better choice and cap the list.

    One is dropped if another candidate in his position costs no more and is
    projected more points. The best scorers are kept regardless, as fallbacks
    for when the club limit rules out the others.
    """
    by_position: dict[int, list[Candidate]] = {}
    for c in candidates:
        by_position.setdefault(c.member.position, []).append(c)

    pool = {}
    for position, cands in by_position.items():
        by_points = sorted(
            cands, key=lambda c: (-c.expected_points, c.member.price, c.member.player_id)
        )
        keep = {c.member.player_id for c in by_points[:KEEP_TOP_PER_POSITION]}
        # Cheapest first; keep a player only if he beats everyone cheaper.
        best_so_far = float("-inf")
        for c in sorted(
            cands, key=lambda c: (c.member.price, -c.expected_points, c.member.player_id)
        ):
            if c.expected_points > best_so_far:
                keep.add(c.member.player_id)
                best_so_far = c.expected_points
        kept = [c for c in by_points if c.member.player_id in keep]
        pool[position] = kept[:POOL_PER_POSITION]
    return pool

import random
from itertools import combinations, product

import pytest

from fpl_data import lineup, optimizer, squad
from fpl_data.optimizer import Candidate, Request, suggest
from fpl_data.squad import Member
from tests.helpers import valid_squad


def candidate(
    player_id: int, position: int, points: float, price: int = 50, club_id: int = 20
) -> Candidate:
    return Candidate(
        Member(player_id, f"C{player_id}", position, club_id, f"Club{club_id}", price), points
    )


def request(points=None, candidates=(), free=1, max_transfers=3, max_hit=None, bank=10) -> Request:
    """A request for valid_squad() where every squad player is projected 3 points,
    except those listed in `points` (player id -> points)."""
    squad_ = valid_squad(bank)
    expected = {m.player_id: 3.0 for m in squad_.members} | (points or {})
    return Request(squad_, free, max_transfers, max_hit, expected, list(candidates))


def test_the_obvious_upgrade_is_found():
    # Forward 13 is the weakest (1 point); a 10-point forward is available at the same price.
    req = request(points={13: 1.0}, candidates=[candidate(100, 4, 10.0)])

    best = suggest(req)[0]

    assert (best.out_ids, best.in_ids, best.hit) == ([13], [100], 0)
    # Before: 13 sits on the bench (a 3-5-2 of 3-point players scores 33). After: the new
    # forward starts, and a 3-5-2 with a 10 and a 3 up front scores 40.
    assert best.expected_gain == 7.0


def test_upgrading_a_player_who_would_only_sit_on_the_bench_gains_nothing():
    # Goalkeeper 2 is the substitute (0 points). A 2-point goalkeeper would still not
    # beat the 3-point first choice, so the eleven doesn't change.
    req = request(points={2: 0.0}, candidates=[candidate(100, 1, 2.0)])
    assert suggest(req) == []


def test_a_new_player_good_enough_to_start_gains_the_difference_to_whoever_he_displaces():
    # A 20-point goalkeeper takes the first-choice goalkeeper's place (3 points): +17.
    req = request(points={2: 0.0}, candidates=[candidate(100, 1, 20.0)])
    best = suggest(req)[0]
    assert (best.in_ids, best.expected_gain) == ([100], 17.0)
    assert best.out_ids == [2]  # sell the useless substitute, not the starter


def test_no_beneficial_transfer_gives_an_empty_list():
    req = request(candidates=[candidate(100, 4, 2.0)])  # worse than anyone he could replace
    assert suggest(req) == []


def test_a_replacement_must_be_in_the_same_position():
    # A great goalkeeper cannot replace a forward: the squad would have 3 GK and 2 FWD.
    req = request(points={13: 0.0, 14: 0.0, 15: 0.0}, candidates=[candidate(100, 1, 20.0)])
    for s in suggest(req):
        assert 100 not in s.in_ids or set(s.out_ids) & {1, 2}  # he may only replace a goalkeeper


def test_a_hit_is_taken_only_when_it_pays_for_itself():
    # Two 10-point forwards. One free transfer gains 7 (a 3-5-2 with a 10 up front: 40
    # against 33). Both gain 14 (47 against 33) and cost a 4-point hit: net 10.
    req = request(
        points={13: 1.0, 14: 1.0},
        candidates=[candidate(100, 4, 10.0), candidate(101, 4, 10.0, club_id=21)],
        free=1,
    )

    best = suggest(req)[0]

    assert len(best.in_ids) == 2
    assert (best.hit, best.expected_gain) == (4, 10.0)

    # With a hit not accepted, only the single free transfer is possible.
    best = suggest(
        request(points={13: 1.0, 14: 1.0}, candidates=req.candidates, free=1, max_hit=0)
    )[0]
    assert (len(best.in_ids), best.hit, best.expected_gain) == (1, 0, 7.0)


def test_a_hit_that_barely_pays_for_itself_ranks_below_the_free_transfer():
    # A 10-point and a 4-point forward. One transfer gains 7; both gain 8 (41 against 33),
    # but the hit leaves 4: still positive, yet worse than the single free transfer.
    req = request(
        points={13: 1.0, 14: 1.0},
        candidates=[candidate(100, 4, 10.0), candidate(101, 4, 4.0, club_id=21)],
    )
    results = suggest(req)

    assert (len(results[0].in_ids), results[0].expected_gain) == (1, 7.0)
    two = [s for s in results if len(s.in_ids) == 2]
    assert two and all(s.expected_gain < results[0].expected_gain for s in two)


def test_a_hit_that_costs_more_than_it_gains_is_never_suggested():
    # Upgrades worth +3 and +0.5 together make 3.5, which doesn't cover a 4-point
    # hit (net -0.5): only single transfers may appear.
    req = request(
        points={13: 1.0, 14: 3.0},
        candidates=[candidate(100, 4, 4.0), candidate(101, 4, 3.5, club_id=21)],
    )
    results = suggest(req)

    assert results
    assert all(len(s.in_ids) == 1 for s in results)


def test_the_budget_is_respected():
    # A 10-point forward costs £9.0m: £4.0m more than the £5.0m player sold, with only £1.0m spare.
    pricey = candidate(100, 4, 10.0, price=90)
    cheaper = candidate(101, 4, 7.0, price=60)
    req = request(points={13: 1.0}, candidates=[pricey, cheaper])

    best = suggest(req)[0]

    assert best.in_ids == [101]  # the one he can afford
    assert all(100 not in s.in_ids for s in suggest(req))


def test_selling_a_player_frees_money_for_a_pricier_one():
    # Selling the £5.0m forward and buying a £5.9m one needs only 0.9 of the 1.0 spare.
    req = request(points={13: 1.0}, candidates=[candidate(100, 4, 8.0, price=59)])
    assert suggest(req)[0].in_ids == [100]


def test_the_club_limit_is_respected():
    # The squad has 2 players from club 1; a third and fourth would break the limit of 3
    # if two come in without anyone leaving that club.
    req = request(
        points={13: 0.0, 14: 0.0},
        candidates=[
            candidate(100, 4, 9.0, club_id=1),
            candidate(101, 4, 9.0, club_id=1),
            candidate(102, 4, 5.0),
        ],
        free=2,
    )
    for s in suggest(req):
        incoming_club_1 = sum(1 for i in s.in_ids if i in (100, 101))
        outgoing_club_1 = sum(1 for o in s.out_ids if o in (1, 9))  # the squad's club 1 players
        assert 2 + incoming_club_1 - outgoing_club_1 <= squad.MAX_PER_CLUB


def test_every_suggestion_is_a_legal_squad():
    rng = random.Random(7)
    candidates = [
        candidate(
            100 + i,
            rng.choice([1, 2, 3, 4]),
            rng.uniform(0, 12),
            rng.randint(40, 70),
            rng.randint(1, 10),
        )
        for i in range(40)
    ]
    req = request(
        points={m.player_id: rng.uniform(0, 8) for m in valid_squad().members},
        candidates=candidates,
        free=2,
    )

    suggestions = suggest(req)

    assert suggestions
    by_id = {c.member.player_id: c.member for c in candidates}
    for s in suggestions:
        _, problems = squad.apply(req.squad, s.out_ids, [by_id[i] for i in s.in_ids])
        assert problems == []


def test_results_are_best_first_and_limited():
    req = request(
        points={13: 0.0, 14: 0.0, 15: 0.0},
        candidates=[candidate(100 + i, 4, 4.0 + i, club_id=10 + i) for i in range(8)],
    )
    results = suggest(req, limit=3)
    gains = [s.expected_gain for s in results]
    assert len(results) == 3
    assert gains == sorted(gains, reverse=True)


def test_pruning_drops_players_who_are_never_the_better_choice():
    cands = [
        candidate(1, 4, 9.0, price=50),
        candidate(2, 4, 8.0, price=60),  # dearer AND worse than #1
        candidate(3, 4, 7.0, price=45),  # cheaper than #1, so worth keeping
        candidate(4, 4, 6.0, price=55),
        candidate(5, 4, 5.0, price=58),
        candidate(6, 4, 1.0, price=70),  # dearer and worse than everyone: dropped
    ]
    kept = {c.member.player_id for c in optimizer._prune(cands)[4]}
    assert 6 not in kept
    assert {1, 3} <= kept
    assert kept >= {1, 2, 3, 4}  # the top 4 scorers are always kept (club-limit fallbacks)


@pytest.mark.parametrize("seed", range(12))
def test_it_finds_the_same_best_as_checking_every_possibility(seed):
    """The pruning and bounds must not lose the best answer: compare with a plain
    brute force that tries every swap of up to 2 players with no shortcuts, scoring
    each with the same best-eleven rule."""
    rng = random.Random(seed)
    squad_ = valid_squad(bank=rng.randint(0, 30))
    expected = {m.player_id: rng.uniform(0, 8) for m in squad_.members}
    candidates = [
        candidate(
            100 + i,
            rng.choice([1, 2, 3, 4]),
            rng.uniform(0, 12),
            rng.randint(40, 70),
            rng.randint(1, 8),
        )
        for i in range(14)
    ]
    free = rng.randint(1, 2)
    req = Request(squad_, free, 2, None, expected, candidates)

    best_found = max((s.expected_gain for s in suggest(req)), default=0.0)

    points = expected | {c.member.player_id: c.expected_points for c in candidates}
    before = lineup.squad_points(squad_.members, points)
    best_possible = 0.0
    for count in (1, 2):
        hit = squad.hit_cost(count, free)
        for sold in combinations(squad_.members, count):
            for bought in product(candidates, repeat=count):
                if len({c.member.player_id for c in bought}) < count:
                    continue
                after, problems = squad.apply(
                    squad_, [m.player_id for m in sold], [c.member for c in bought]
                )
                if problems:
                    continue
                gain = lineup.squad_points(after.members, points) - before - hit
                best_possible = max(best_possible, gain)

    assert best_found == pytest.approx(best_possible)

import pytest

from fpl_data import squad
from fpl_data.squad import Member, Squad
from tests.helpers import valid_squad


def forward(player_id: int, price: int, club_id: int = 9) -> Member:
    return Member(player_id, "Fwd", 4, club_id, f"Club{club_id}", price)


def test_a_valid_squad_has_no_problems():
    assert squad.validate(valid_squad()) == []


def break_by_removing_a_player(s: Squad) -> None:
    del s.members[14]


def break_position_mix(s: Squad) -> None:
    s.members[2] = Member(3, "PC", 3, 3, "Club3", 50)  # a defender becomes a midfielder


def break_club_limit(s: Squad) -> None:
    s.members[:] = [
        Member(m.player_id, m.name, m.position, 9, "Arsenal", m.price) if i < 4 else m
        for i, m in enumerate(s.members)
    ]


def break_budget(s: Squad) -> None:
    s.bank = -3


def break_duplicate(s: Squad) -> None:
    first = s.members[0]
    s.members[1] = Member(first.player_id, first.name, 1, first.club_id, first.club_name, 50)


@pytest.mark.parametrize(
    ("break_rule", "expected_text"),
    [
        (break_by_removing_a_player, "has 14 players"),
        (break_position_mix, "6 midfielders"),
        (break_club_limit, "4 Arsenal players"),
        (break_budget, "£0.3m more than the budget"),
        (break_duplicate, "twice"),
    ],
)
def test_each_broken_rule_is_reported(break_rule, expected_text):
    s = valid_squad()
    break_rule(s)
    assert any(expected_text in problem for problem in squad.validate(s))


def test_apply_a_legal_transfer():
    s = valid_squad()
    # Sell forward 13 (£5.0m) for a £5.5m forward: the bank goes 1.0 -> 0.5.
    new_squad, problems = squad.apply(s, [13], [forward(100, 55)])

    assert problems == []
    assert new_squad.bank == 5
    assert len(new_squad.members) == squad.SIZE
    # The original squad must be untouched (apply returns a new one).
    assert s.bank == 10
    assert s.members[12].player_id == 13


@pytest.mark.parametrize(
    ("out_ids", "incoming", "expected_text"),
    [
        ([13, 14], [forward(100, 50)], "2 players out but 1 in"),
        ([99], [forward(100, 50)], "not in the squad"),
        ([13], [forward(14, 50)], "already in the squad"),
        ([13], [Member(100, "Mid", 3, 9, "Club9", 50)], "6 midfielders"),
        ([13], [forward(100, 70)], "£1.0m more than the budget"),
    ],
    ids=[
        "counts differ",
        "selling a stranger",
        "buying an owned player",
        "wrong position",
        "can't afford",
    ],
)
def test_apply_reports_problems(out_ids, incoming, expected_text):
    _, problems = squad.apply(valid_squad(), out_ids, incoming)
    assert any(expected_text in problem for problem in problems)


def test_the_club_limit_counts_new_players():
    # Club 1 already has players 1 and 9. Bringing in two more Club 1 players
    # (for forwards from other clubs) makes 4.
    incoming = [forward(100, 50, club_id=1), forward(101, 50, club_id=1)]
    _, problems = squad.apply(valid_squad(), [14, 15], incoming)
    assert any("4 Club1 players" in problem for problem in problems)


@pytest.mark.parametrize(
    ("transfers", "free", "chip", "expected"),
    [
        (1, 1, "", 0),  # free
        (2, 1, "", 4),  # one extra
        (3, 1, "", 8),  # two extra
        (0, 2, "", 0),  # none made
        (5, 1, squad.CHIP_WILDCARD, 0),  # a Wildcard makes all free
        (5, 1, squad.CHIP_FREE_HIT, 0),
        (2, 1, "bboost", 4),  # Bench Boost doesn't
    ],
)
def test_hit_cost(transfers, free, chip, expected):
    assert squad.hit_cost(transfers, free, chip) == expected

import pytest

from fpl_data.db import players, teams
from fpl_data.db.players import PlayerFilter, PlayerSort


@pytest.mark.parametrize(
    ("search", "first"),
    [
        ("saka", "Saka"),  # beats Wan-Bissaka and Sakamoto on points
        ("SAKA", "Saka"),  # case doesn't matter
        ("bukayo", "Saka"),  # first names are searchable
        ("odegaard", "Ødegaard"),  # accents don't matter...
        ("Ødegaard", "Ødegaard"),  # ...in either direction
    ],
)
def test_search_finds_the_likeliest_player_first(seeded_store, search, first):
    found = players.search(seeded_store, search, 5)
    assert found[0].web_name == first


def test_search_orders_by_points_and_respects_limit(seeded_store):
    found = players.search(seeded_store, "wilson", 3)
    assert len(found) == 3
    points = [p.total_points for p in found]
    assert points == sorted(points, reverse=True)


def test_search_with_no_match_is_empty(seeded_store):
    assert players.search(seeded_store, "zzzz", 5) == []


def test_get_player(seeded_store):
    saka = players.get_player(seeded_store, 12)
    assert (saka.web_name, saka.team_name, saka.position) == ("Saka", "Arsenal", 3)
    assert players.get_player(seeded_store, 99999) is None


def test_players_by_id(seeded_store):
    found = players.players_by_id(seeded_store, [12, 10, 99999])
    # The unknown id is simply missing.
    assert {pid: p.web_name for pid, p in found.items()} == {12: "Saka", 10: "White"}
    assert players.players_by_id(seeded_store, []) == {}


def test_find_players_filters_and_sorts(seeded_store):
    # The best midfielders at £8.0m or less by expected points.
    found = players.find_players(
        seeded_store,
        PlayerFilter(position=3, max_cost=80, sort=PlayerSort.EXPECTED_POINTS, limit=10),
    )
    assert len(found) == 10
    for p in found:
        assert p.position == 3
        assert p.now_cost <= 80
        assert p.status in ("a", "d")  # unavailable players are left out
    expected = [p.expected_points_next for p in found]
    assert expected == sorted(expected, reverse=True)


def test_find_players_exclude_and_include(seeded_store):
    # The best player by total points, then the same search without him.
    best = players.find_players(seeded_store, PlayerFilter(sort=PlayerSort.TOTAL_POINTS, limit=1))[
        0
    ]
    flt = PlayerFilter(sort=PlayerSort.TOTAL_POINTS, limit=1, exclude_ids=[best.id])
    assert players.find_players(seeded_store, flt)[0].id != best.id

    # Saliba (6) is injured: hidden by default, found when asked for.
    defenders = PlayerFilter(position=2, sort=PlayerSort.TOTAL_POINTS, limit=1000)
    hidden = players.find_players(seeded_store, defenders)
    defenders.include_unavailable = True
    everyone = players.find_players(seeded_store, defenders)
    assert 6 not in [p.id for p in hidden]
    assert 6 in [p.id for p in everyone]


def test_top_transfers(seeded_store):
    most_in = players.top_transfers(seeded_store, True, 3)
    # From the saved data: Groß was the most transferred in.
    assert (most_in[0].web_name, most_in[0].transfers_in_event) == ("Groß", 763845)

    most_out = players.top_transfers(seeded_store, False, 3)
    assert most_out[0].transfers_out_event >= most_out[1].transfers_out_event


def test_teams(seeded_store):
    all_teams = teams.all_teams(seeded_store)
    assert len(all_teams) == 20
    assert (all_teams[0].name, all_teams[0].short_name) == ("Arsenal", "ARS")

    assert [teams.team_exists(seeded_store, i) for i in (1, 20, 21, 0)] == [
        True,
        True,
        False,
        False,
    ]

from fpl_data.db import player_prices


def test_a_double_gameweek_is_saved_once(seeded_store):
    # /element-summary/ has one history entry per match: two for gameweek 2 here.
    player_prices.save_prices(seeded_store, 12, [(1, 95), (2, 96), (2, 96), (3, 97)])

    assert [player_prices.price_at(seeded_store, 12, gw) for gw in (1, 2, 3)] == [95, 96, 97]


def test_saving_again_replaces_the_history(seeded_store):
    player_prices.save_prices(seeded_store, 12, [(1, 95)])
    player_prices.save_prices(seeded_store, 12, [(1, 100)])

    assert player_prices.price_at(seeded_store, 12, 1) == 100
    assert player_prices.has_prices(seeded_store, 12)

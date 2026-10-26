"""The SQLite cache.

One module per kind of data; each has plain functions that take the `Store`:

    store.py            opening the database, schema versioning, transactions
    sync_log.py         when each dataset was last downloaded
    bootstrap.py        saving teams, gameweeks, chips and players (from /bootstrap-static/)
    players.py          player lookups, filtered search, transfer trends
    teams.py            team lookups
    gameweeks.py        gameweeks and chip allowances
    fixtures.py         fixtures (from /fixtures/)
    player_gameweeks.py players' stats per gameweek (from /event/{gw}/live/)
    manager_teams.py    managers' teams, saved until the next deadline
    player_prices.py    players' price in each gameweek (for purchase prices)
    transfer_snapshots.py  how many managers bought/sold each player over time

The functions return small dataclasses ("rows"), never protobuf messages, so
this package doesn't depend on gRPC; the handlers convert.
"""

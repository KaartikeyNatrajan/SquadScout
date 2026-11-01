"""Past Premier League results, for head-to-head questions ("Arsenal play Liverpool
next: how have their recent meetings gone, and who scored?").

FPL's API only covers the current season. Past seasons come from a community
archive of FPL's own data (github.com/vaastav/Fantasy-Premier-League), saved
every season in FPL's format, so both sources go through the same code.

    seasons.py   season labels ("2025-26") and which seasons we keep
    results.py   FPL fixtures + their stats -> results with scorers, assists, ...
    archive.py   downloading and reading a past season from the archive
    refresh.py   the job that downloads the past seasons we don't have yet

The current season's results are saved by the fixtures refresh (refresh.py at
the package root). Head-to-head records are worked out in head_to_head.py.
"""

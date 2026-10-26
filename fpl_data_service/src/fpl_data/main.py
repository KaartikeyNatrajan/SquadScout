"""Wiring and startup.

Startup order: open the SQLite database -> download any stale FPL data ->
start the gRPC server -> keep refreshing in the background until Ctrl+C.
"""

import asyncio
import logging
import signal
from datetime import timedelta

from fpl_data.config import Settings
from fpl_data.db.store import Store
from fpl_data.fpl.client import FplClient, RateLimitedError
from fpl_data.handlers.service import FplService
from fpl_data.manager.loader import Loader
from fpl_data.news.client import new_http_client
from fpl_data.news.refresh import NewsRefresher
from fpl_data.refresh import Refresher
from fpl_data.server import start_server

logger = logging.getLogger(__name__)

# Data older than this is considered stale; FPL changes prices daily and points
# after matches.
MAX_AGE = timedelta(hours=1)
# Check for stale data this often. Checking more often than MAX_AGE means data
# is never much more than an hour old, and most checks only read sync_log.
CHECK_EVERY = timedelta(minutes=10)
# After a failure, retry sooner: if the store is empty (first start, or just
# after a schema change), every request fails until a refresh succeeds. (If the
# FPL API asked us to wait longer than this, we wait as long as it asked.)
RETRY_AFTER_FAILURE = timedelta(minutes=1)
# Stops a hanging FPL API from blocking startup (or the next check) forever.
# Progress is saved per gameweek, so a timed-out first run carries on next time.
REFRESH_TIMEOUT = timedelta(minutes=2)


async def refresh_all(refresher: Refresher) -> timedelta:
    """Update players, teams, fixtures and gameweek stats if they're stale, and
    return how long to wait before checking again.

    Failures only log a warning: serving slightly old data beats not serving at
    all when the FPL API is down (or rate-limiting us with 429s).
    """
    try:
        async with asyncio.timeout(REFRESH_TIMEOUT.total_seconds()):
            await refresher.all()
    except Exception as err:  # includes TimeoutError
        wait = RETRY_AFTER_FAILURE
        # The refresher wraps the real error; if FPL told us to slow down, obey.
        if isinstance(err.__cause__, RateLimitedError):
            wait = max(wait, timedelta(seconds=err.__cause__.retry_after))
        logger.warning("refresh failed, serving cached data; retrying in %s: %s", wait, err)
        return wait
    return CHECK_EVERY


async def refresh_forever(refresher: Refresher, wait: timedelta) -> None:
    """Re-check for stale data on a timer, until cancelled."""
    while True:
        await asyncio.sleep(wait.total_seconds())
        wait = await refresh_all(refresher)


# The news feeds are read on their own timer, apart from the FPL refresh, so a
# broken feed never delays FPL data (or the other way round). NewsRefresher
# skips the work unless its data is over an hour old.
NEWS_CHECK_EVERY = timedelta(minutes=10)


async def news_forever(news_refresher: NewsRefresher) -> None:
    """Read the news feeds roughly hourly, until cancelled. Failures are only logged."""
    while True:
        try:
            await news_refresher.refresh()
        except Exception as err:
            logger.warning("news refresh failed: %s", err)
        await asyncio.sleep(NEWS_CHECK_EVERY.total_seconds())


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # it logs every request at INFO
    settings = Settings.from_env()

    store = Store.open(settings.db_path)
    client = FplClient(settings.fpl_base_url)
    refresher = Refresher(store, client, MAX_AGE)
    # Managers' teams are the one thing downloaded during a request: the first
    # time each team is asked about, then saved until the next deadline.
    managers = Loader(client, store)

    wait = await refresh_all(refresher)  # before serving, so the first requests see fresh data

    server, _ = await start_server(FplService(store, managers), settings.grpc_addr)
    logger.info("fpl_data_service listening: addr=%s db=%s", settings.grpc_addr, settings.db_path)
    refresh_task = asyncio.create_task(refresh_forever(refresher, wait))
    news_http = new_http_client()
    news_task = asyncio.create_task(news_forever(NewsRefresher(store, news_http)))

    # Wait for Ctrl+C (SIGINT) or SIGTERM (what Docker or `kill` send).
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    logger.info("shutting down")
    refresh_task.cancel()
    news_task.cancel()
    await server.stop(grace=5)  # lets in-flight requests finish first
    await client.aclose()
    await news_http.aclose()
    store.close()

"""The hourly news job: read every feed, keep the stories that matter, save them."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx

from fpl_data.db import news, players, sync_log, teams
from fpl_data.db.store import Store
from fpl_data.news import feeds, pipeline
from fpl_data.news.client import fetch_all
from fpl_data.news.matching import Matcher

logger = logging.getLogger(__name__)

MAX_AGE = timedelta(hours=1)  # feeds are read at most this often


class NewsRefreshError(Exception):
    """No feed could be read."""


class NewsRefresher:
    def __init__(self, store: Store, http: httpx.AsyncClient, max_age: timedelta = MAX_AGE):
        self.store = store
        self.http = http
        self.max_age = max_age

    async def refresh(self, force: bool = False) -> int:
        """Read the feeds if they're stale. Returns how many new stories were saved.

        Raises NewsRefreshError if every feed failed (the network or Google is down):
        then nothing is recorded, so the next check tries again instead of waiting an hour.
        """
        last = sync_log.last_synced(self.store, news.DATASET)
        if not force and last is not None and datetime.now(UTC) - last < self.max_age:
            return 0
        all_teams = teams.all_teams(self.store)
        if not all_teams:  # FPL data isn't loaded yet; there is nothing to match against
            return 0
        everyone = players.find_players(
            self.store, players.PlayerFilter(include_unavailable=True, limit=5000)
        )
        matcher = Matcher(everyone, all_teams)

        sources = feeds.all_feeds(all_teams)
        raw, failed = await fetch_all(self.http, sources)
        if len(failed) == len(sources):
            raise NewsRefreshError(f"all {len(sources)} news feeds failed")
        # Matching a few thousand items takes a second or two of CPU; a worker
        # thread keeps the event loop free to answer requests meanwhile.
        stories = await asyncio.to_thread(pipeline.process, raw, matcher)
        added = news.save(self.store, stories)
        logger.info(
            "news refreshed: %d feed items, %d new stories, %d feeds failed",
            len(raw),
            added,
            len(failed),
        )
        return added

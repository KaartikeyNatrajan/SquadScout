"""The hourly news job: read every feed, keep the stories that matter, save them."""

import logging
from datetime import timedelta

import httpx

from fpl_data.db import news, players, sync_log, teams
from fpl_data.db.store import Store
from fpl_data.news import feeds, pipeline
from fpl_data.news.client import fetch_all
from fpl_data.news.matching import Matcher

logger = logging.getLogger(__name__)

MAX_AGE = timedelta(hours=1)  # feeds are read at most this often


class NewsRefresher:
    def __init__(self, store: Store, http: httpx.AsyncClient, max_age: timedelta = MAX_AGE):
        self.store = store
        self.http = http
        self.max_age = max_age

    async def refresh(self, force: bool = False) -> int:
        """Read the feeds if they're stale. Returns how many new stories were saved."""
        from datetime import UTC, datetime

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

        raw, failed = await fetch_all(self.http, feeds.all_feeds(all_teams))
        added = news.save(self.store, pipeline.process(raw, matcher))
        logger.info(
            "news refreshed: %d feed items, %d new stories, %d feeds failed",
            len(raw),
            added,
            len(failed),
        )
        return added

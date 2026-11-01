"""Downloading the past seasons we don't have yet.

Past seasons never change, so each is downloaded once (about 10 seconds for
all five) and then only again if the cache is rebuilt. The current season is
handled by the fixtures refresh instead.
"""

import logging

from fpl_data.db import results
from fpl_data.db.store import Store
from fpl_data.history import seasons
from fpl_data.history.archive import ArchiveClient, ArchiveError

logger = logging.getLogger(__name__)


class HistoryRefreshError(Exception):
    """At least one season couldn't be downloaded; the others were saved."""


class HistoryRefresher:
    def __init__(self, store: Store, archive: ArchiveClient):
        self.store = store
        self.archive = archive

    async def refresh(self) -> list[str]:
        """Download each missing past season. Returns the seasons saved.

        A season that fails doesn't stop the others; HistoryRefreshError is raised
        at the end so the caller retries later."""
        saved, failed = [], []
        for season in seasons.searched(self.store)[1:]:
            if results.has_season(self.store, season):
                continue
            try:
                season_results = await self.archive.fetch_season(season)
            except ArchiveError as err:
                logger.warning("past season %s failed: %s", season, err)
                failed.append(season)
                continue
            results.save_season(self.store, season, season_results)
            logger.info("past season saved: season=%s matches=%d", season, len(season_results))
            saved.append(season)
        if failed:
            raise HistoryRefreshError(f"couldn't download seasons {', '.join(failed)}")
        return saved

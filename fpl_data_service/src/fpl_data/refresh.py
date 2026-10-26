"""Deciding when cached FPL data is stale, and re-downloading it.

main.py runs `Refresher.all` on startup and on a timer, so requests are
answered from SQLite without waiting on the FPL API. There are three kinds of
download:

    bootstrap   teams, gameweeks, chips, players   GET /bootstrap-static/
    fixtures    every fixture of the season        GET /fixtures/
    gameweeks   every player's stats, per week     GET /event/{gw}/live/

Each is skipped if sync_log says it was saved recently enough.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from fpl_data.db import fixtures, gameweeks, player_gameweeks, sync_log
from fpl_data.db.bootstrap import upsert_bootstrap
from fpl_data.db.store import Store
from fpl_data.fpl.client import FplClient

logger = logging.getLogger(__name__)

# A maximum age that's never reached: data saved once stays fresh forever.
NEVER = timedelta.max


class RefreshError(Exception):
    """A download or save failed. The message says which dataset."""


class Refresher:
    """Downloads FPL data into the store when the stored copy is older than `max_age`."""

    def __init__(self, store: Store, client: FplClient, max_age: timedelta):
        self.store = store
        self.client = client
        self.max_age = max_age

    async def all(self) -> None:
        """Refresh everything, in dependency order: bootstrap first (the rest
        refer to its teams, players and gameweeks), then fixtures (the gameweek
        stats are joined with them), then gameweek stats. If one step fails,
        the later ones are skipped and the error is raised."""
        await self.bootstrap()
        await self.fixtures()
        await self.gameweeks()

    async def bootstrap(self) -> None:
        """Re-download teams, gameweeks, chips and players if they're stale."""

        async def download() -> None:
            data = await self.client.fetch_bootstrap()
            upsert_bootstrap(self.store, data)
            logger.info(
                "bootstrap refreshed: players=%d teams=%d gameweeks=%d",
                len(data.elements),
                len(data.teams),
                len(data.events),
            )

        await self._if_stale(sync_log.BOOTSTRAP, self.max_age, download)

    async def fixtures(self) -> None:
        """Re-download every fixture if they're stale."""

        async def download() -> None:
            data = await self.client.fetch_fixtures()
            fixtures.upsert_fixtures(self.store, data)
            logger.info("fixtures refreshed: fixtures=%d", len(data))

        await self._if_stale(sync_log.FIXTURES, self.max_age, download)

    async def gameweeks(self) -> None:
        """Download every player's stats for each gameweek played so far.

        The current gameweek and the one before it can still change (points
        come in during matches; bonus points and corrections arrive a day or so
        later), so those two are refreshed like everything else. Older
        gameweeks are final: each is downloaded once and kept.

        Each gameweek is saved and recorded separately, so if the API fails
        half way, the next run carries on from where this one stopped.
        """
        current = gameweeks.current_gameweek(self.store)
        if current is None:
            return  # the season hasn't started: nothing played yet

        for gameweek in range(1, current + 1):
            max_age = self.max_age if gameweek >= current - 1 else NEVER
            await self._if_stale(
                sync_log.gameweek_stats_dataset(gameweek),
                max_age,
                self._download_gameweek(gameweek),
            )

    def _download_gameweek(self, gameweek: int) -> Callable[[], Awaitable[None]]:
        async def download() -> None:
            live = await self.client.fetch_event_live(gameweek)
            player_gameweeks.upsert_gameweek_stats(self.store, gameweek, live)
            logger.info(
                "gameweek stats refreshed: gameweek=%d players=%d", gameweek, len(live.elements)
            )

        return download

    async def _if_stale(
        self, dataset: str, max_age: timedelta, download: Callable[[], Awaitable[None]]
    ) -> None:
        """Run `download` only if `dataset` was never saved, or was last saved
        `max_age` or more ago. This is the one place the freshness rule lives.

        `download` is a function passed in as a value: each caller hands over
        "how to download my dataset", and this decides whether to call it.
        """
        last = sync_log.last_synced(self.store, dataset)
        if last is not None and datetime.now(UTC) - last < max_age:
            logger.debug("fresh, skipping download: dataset=%s synced_at=%s", dataset, last)
            return
        try:
            await download()
        except Exception as err:
            raise RefreshError(f"refresh {dataset}: {err}") from err

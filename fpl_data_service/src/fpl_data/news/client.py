"""Downloading feeds. One feed failing never stops the others."""

import asyncio
import logging

import httpx

from fpl_data.news.models import Feed, RawItem
from fpl_data.news.rss import FeedError, parse_rss

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; squad-scout/0.1)"
CONCURRENCY = 3  # be gentle: Google News in particular rate-limits
TIMEOUT_S = 15.0


def new_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S, follow_redirects=True
    )


async def fetch_all(http: httpx.AsyncClient, feeds: list[Feed]) -> tuple[list[RawItem], list[str]]:
    """The items of every feed that could be read, and the names of the feeds that couldn't."""
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(feed: Feed) -> list[RawItem] | None:
        async with gate:
            try:
                response = await http.get(feed.url)
                response.raise_for_status()
                return parse_rss(response.text, feed)
            except (httpx.HTTPError, FeedError) as err:
                logger.warning("news feed %s failed: %s", feed.name, err)
                return None

    results = await asyncio.gather(*(one(f) for f in feeds))
    items = [i for r in results if r for i in r]
    failed = [f.name for f, r in zip(feeds, results, strict=True) if r is None]
    return items, failed

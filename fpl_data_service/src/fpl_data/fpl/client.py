"""HTTP client for the FPL API.

Endpoints we use (all GET, no auth):

    /bootstrap-static/              all players ("elements"), teams, gameweeks ("events"), chips
    /fixtures/                      every fixture of the season, with difficulty ratings
    /event/{gw}/live/               every player's stats in one gameweek
    /element-summary/{id}/          one player's history; we only use his price in each gameweek
    /entry/{id}/                    one manager's team, plus /history/, /transfers/
    /entry/{id}/event/{gw}/picks/   the 15 players picked for one gameweek

This module only downloads and decodes JSON. Deciding when to download
(refresh.py) and storing the result (db/) live elsewhere.

The FPL API answers HTTP 429 ("too many requests") when it's hammered, so the
client never has more than a few requests in flight, and waits and retries when
it is told to slow down.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx
from pydantic import TypeAdapter, ValidationError

from fpl_data.fpl import models

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://fantasy.premierleague.com/api"

# The FPL API sometimes returns 403 to requests without a browser-like UA.
USER_AGENT = "Mozilla/5.0 (compatible; squad-scout/0.1)"

# Never more than this many requests in flight at once, because a manager's
# first request makes several at the same time.
MAX_CONCURRENT_REQUESTS = 4
# When the API says 429, we retry up to this many times, waiting as long as its
# Retry-After header asks (or a short backoff if it doesn't say)...
MAX_RETRIES = 2
# ...but only if that wait is at most this many seconds. A longer wait would
# leave a caller (maybe an agent waiting for an answer) hanging, so we raise
# RateLimitedError instead and let the caller decide.
MAX_RETRY_WAIT = 10.0


class FplError(Exception):
    """The FPL API couldn't be reached, or answered with an error or unexpected data."""


class NotFoundError(FplError):
    """The API answered 404, e.g. for an unknown team id or a gameweek not published yet."""


class RateLimitedError(FplError):
    """The API answered 429 ("too many requests") and asked us to wait too long to wait here.

    `retry_after` is how many seconds it asked for, so a background job can try
    again then.
    """

    def __init__(self, message: str, retry_after: float):
        super().__init__(message)
        self.retry_after = retry_after


class FplClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        """base_url is normally DEFAULT_BASE_URL. Tests pass a fake `transport`
        (see tests/fake_fpl.py) so nothing touches the network, and a fake
        `sleep` so retries don't actually wait."""
        self._sleep = sleep
        self._slots = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=15.0,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def fetch_bootstrap(self) -> models.Bootstrap:
        """Teams, gameweeks, chips and players, in one ~3 MB response."""
        return await self._get("/bootstrap-static/", models.Bootstrap)

    async def fetch_fixtures(self) -> list[models.Fixture]:
        """Every fixture of the season (380 matches)."""
        return await self._get("/fixtures/", list[models.Fixture])

    async def fetch_event_live(self, gameweek: int) -> models.EventLive:
        """Every player's stats for one gameweek."""
        return await self._get(f"/event/{gameweek}/live/", models.EventLive)

    async def fetch_element_summary(self, player_id: int) -> models.ElementSummary:
        """One player's history: his price in every gameweek he has played in."""
        return await self._get(f"/element-summary/{player_id}/", models.ElementSummary)

    async def fetch_entry(self, team_id: int) -> models.Entry:
        """A team's summary. Unknown ids raise NotFoundError."""
        return await self._get(f"/entry/{team_id}/", models.Entry)

    async def fetch_entry_history(self, team_id: int) -> models.EntryHistory:
        """A team's gameweek-by-gameweek history and the chips it has played."""
        return await self._get(f"/entry/{team_id}/history/", models.EntryHistory)

    async def fetch_entry_picks(self, team_id: int, gameweek: int) -> models.EntryPicks:
        """A team's squad for one gameweek. Gameweeks whose deadline hasn't passed raise NotFoundError."""
        return await self._get(f"/entry/{team_id}/event/{gameweek}/picks/", models.EntryPicks)

    async def fetch_entry_transfers(self, team_id: int) -> list[models.EntryTransfer]:
        """Every transfer a team has made this season."""
        return await self._get(f"/entry/{team_id}/transfers/", list[models.EntryTransfer])

    async def _get(self, path: str, model):
        """GET `path` and decode the JSON body as `model` (a pydantic class, or list[class])."""
        response = await self._get_with_retries(path)

        if response.status_code == 404:
            raise NotFoundError(f"GET {path}: not found")
        if response.status_code != 200:
            raise FplError(f"GET {path}: unexpected status {response.status_code}")
        try:
            # TypeAdapter lets us validate `list[Fixture]` as well as a single model.
            return TypeAdapter(model).validate_json(response.content)
        except ValidationError as err:
            raise FplError(f"GET {path}: decode: {err}") from err

    async def _get_with_retries(self, path: str) -> httpx.Response:
        """GET `path`, waiting and trying again when the API says 429 (too many requests)."""
        for attempt in range(MAX_RETRIES + 1):
            try:
                # `async with` holds one of the request slots while the request runs.
                async with self._slots:
                    response = await self._http.get(path)
            except httpx.HTTPError as err:
                raise FplError(f"GET {path}: {err}") from err
            if response.status_code != 429:
                return response

            wait = _retry_after(response, default=1.0 * (attempt + 1))
            if attempt == MAX_RETRIES or wait > MAX_RETRY_WAIT:
                raise RateLimitedError(
                    f"GET {path}: rate limited, asked to wait {wait:g}s", retry_after=wait
                )
            logger.warning("rate limited on %s, waiting %gs (attempt %d)", path, wait, attempt + 1)
            await self._sleep(wait)
        raise AssertionError("unreachable")  # the loop always returns or raises


def _retry_after(response: httpx.Response, default: float) -> float:
    """Seconds the API asked us to wait (its Retry-After header), or `default`."""
    try:
        return max(float(response.headers["retry-after"]), 0.0)
    except (KeyError, ValueError):
        return default  # no header, or a date instead of a number of seconds

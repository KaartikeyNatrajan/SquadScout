"""Season labels: "2025-26" runs from July 2025 to June 2026."""

from datetime import UTC, datetime

from fpl_data.db import fixtures
from fpl_data.db.store import Store

PAST_SEASONS = 5  # completed seasons kept, besides the current one


def season_of(moment: datetime) -> str:
    """The season a date falls in: 2026-02-01 -> "2025-26", 2026-08-15 -> "2026-27"."""
    start = moment.year if moment.month >= 7 else moment.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def current(store: Store) -> str:
    """The season FPL is playing: the one its fixtures are in (today's date
    before the fixtures are downloaded)."""
    first = fixtures.first_kickoff(store)
    return season_of(datetime.fromisoformat(first) if first else datetime.now(UTC))


def searched(store: Store, past: int = PAST_SEASONS) -> list[str]:
    """The current season and the `past` before it, newest first."""
    now = current(store)
    return [now, *previous(now, past)]


def previous(season: str, count: int) -> list[str]:
    """The `count` seasons before `season`, newest first: previous("2026-27", 2) ->
    ["2025-26", "2024-25"]."""
    start = int(season[:4])
    return [f"{y}-{(y + 1) % 100:02d}" for y in range(start - 1, start - 1 - count, -1)]

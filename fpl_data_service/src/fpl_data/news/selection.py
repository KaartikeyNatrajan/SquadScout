"""Choosing which stories to show: recent ones, without near-duplicates.

Several outlets run the same story with slightly different headlines
("Semenyo injury: out until December" / "Semenyo out until December, Maresca
says"). Showing the model three versions would waste its small context and make
one story look like three.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from fpl_data.news.matching import tokens
from fpl_data.news.models import NewsItem

SIMILARITY = 0.6  # headlines sharing this fraction of their words are one story


def _similar(a: set[str], b: set[str]) -> bool:
    return bool(a and b) and len(a & b) / len(a | b) >= SIMILARITY


def select(items: Sequence[NewsItem], limit: int) -> list[NewsItem]:
    """The newest `limit` stories, skipping any whose headline nearly repeats a newer one."""
    chosen: list[NewsItem] = []
    seen: list[set[str]] = []
    for item in sorted(items, key=lambda i: i.published, reverse=True):
        words = set(tokens(item.title))
        if any(_similar(words, other) for other in seen):
            continue
        chosen.append(item)
        seen.append(words)
        if len(chosen) == limit:
            break
    return chosen


def recent(items: Sequence[NewsItem], now: datetime, window: timedelta) -> list[NewsItem]:
    return [i for i in items if now - i.published <= window]

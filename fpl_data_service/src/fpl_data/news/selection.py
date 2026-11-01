"""Choosing which stories to show: the newest, without near-duplicates.

Several outlets run the same story with slightly different headlines
("Semenyo injury: out until December" / "Semenyo out until December, Maresca
says"). Showing the model three versions would waste its small context and make
one story look like three.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, TypeVar

from fpl_data.news.matching import tokens

SIMILARITY = 0.6  # headlines sharing this fraction of their words are one story


class Story(Protocol):
    title: str
    published: datetime


S = TypeVar("S", bound=Story)


def select(stories: Sequence[S], limit: int) -> list[S]:
    """The newest `limit` stories, skipping any whose headline nearly repeats a newer one."""
    chosen: list[S] = []
    seen: list[set[str]] = []
    for story in sorted(stories, key=lambda s: s.published, reverse=True):
        words = set(tokens(story.title))
        if any(_similar(words, other) for other in seen):
            continue
        chosen.append(story)
        seen.append(words)
        if len(chosen) == limit:
            break
    return chosen


def _similar(a: set[str], b: set[str]) -> bool:
    """Whether two headlines (as sets of words) share at least SIMILARITY of their words."""
    return bool(a and b) and len(a & b) / len(a | b) >= SIMILARITY

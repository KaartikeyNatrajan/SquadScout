"""The data that moves through the news code."""

from dataclasses import dataclass, field
from datetime import datetime

# Topics a story can be tagged with (see topics.py).
TOPICS = ("injury", "fitness", "return", "suspension", "rest", "team_news", "press_conference")


@dataclass(frozen=True)
class Feed:
    """Somewhere to read news from."""

    name: str  # for logs, e.g. "BBC Sport"
    url: str
    # Google News gives only a headline (its description repeats the headline and
    # the outlet's name); publishers give a real summary.
    has_summary: bool = True
    # For a feed that searches for one team's news: that team's id. Stories it
    # returns are attached to that team.
    team_id: int | None = None
    # The outlet to credit when the item doesn't say. (Google names the outlet in each item.)
    source: str = ""


@dataclass
class RawItem:
    """One entry of a feed, as published."""

    title: str
    link: str
    published: datetime
    description: str = ""  # a summary, as HTML; may be empty
    source: str = ""  # the outlet
    feed: Feed | None = None


@dataclass
class NewsItem:
    """A story that is about the football we track, ready to store."""

    dedupe_key: str  # the same story from two feeds has the same key
    title: str
    snippet: str  # what the language model is shown (see text.build_snippet)
    source: str
    url: str
    published: datetime
    topics: list[str]
    player_ids: list[int] = field(default_factory=list)
    team_ids: list[int] = field(default_factory=list)

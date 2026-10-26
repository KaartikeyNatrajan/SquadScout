"""Raw feed items -> stories ready to store."""

import re
from collections.abc import Iterable

from fpl_data.news import text, topics
from fpl_data.news.matching import Matcher
from fpl_data.news.models import NewsItem, RawItem

# Women's football and betting previews are not about the players FPL scores.
_NOT_OURS = re.compile(r"\b(wsl|women'?s|lionesses|odds|bet builder|betting)\b", re.I)


def process(raw: Iterable[RawItem], matcher: Matcher) -> list[NewsItem]:
    """Keep stories that are about a player or club *and* have a topic we track,
    each once however many feeds carried it.

    Match reports and transfer gossip carry no topic, so they are dropped here:
    this keeps the store small and the model's context free of noise.
    """
    stories: dict[str, NewsItem] = {}
    for item in raw:
        summary = text.clean_html(item.description) if item.description else ""
        full_text = f"{item.title}. {summary}"
        feed_team = item.feed.team_id if item.feed else None

        if _NOT_OURS.search(full_text):
            continue
        found_topics, trigger_words = topics.tag(full_text)
        match = matcher.find(full_text, feed_team)
        if not found_topics or not (match.player_ids or match.team_ids):
            continue

        key = text.title_key(item.title)
        if key in stories:
            continue
        focus = [matcher_word for matcher_word in trigger_words]
        stories[key] = NewsItem(
            dedupe_key=key,
            title=item.title,
            snippet=text.build_snippet(item.title, summary, focus),
            source=item.source,
            url=item.link,
            published=item.published,
            topics=found_topics,
            player_ids=match.player_ids,
            team_ids=match.team_ids,
        )
    return list(stories.values())

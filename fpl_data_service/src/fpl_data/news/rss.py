"""Parsing RSS 2.0 feeds."""

import xml.etree.ElementTree as ET
from datetime import UTC
from email.utils import parsedate_to_datetime

from fpl_data.news.models import Feed, RawItem
from fpl_data.news.text import strip_outlet

# Feeds are a few hundred KB. Refuse anything far bigger than that.
MAX_FEED_BYTES = 3_000_000


class FeedError(Exception):
    """A feed couldn't be downloaded or read."""


def parse_rss(xml: str, feed: Feed) -> list[RawItem]:
    """The items of an RSS feed. Items without a title, link or valid date are skipped:
    without a date we can't tell whether a story is still current."""
    if len(xml) > MAX_FEED_BYTES:
        raise FeedError(f"{feed.name}: feed is {len(xml)} bytes, more than {MAX_FEED_BYTES}")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as err:
        raise FeedError(f"{feed.name}: not valid XML: {err}") from err

    items = []
    for node in root.iter("item"):
        title = _text(node, "title")
        link = _text(node, "link")
        published = _date(_text(node, "pubDate"))
        if not title or not link or published is None:
            continue
        source = _text(node, "source") or feed.source
        items.append(
            RawItem(
                title=strip_outlet(title, source),
                link=link,
                published=published,
                description="" if not feed.has_summary else _text(node, "description"),
                source=source,
                feed=feed,
            )
        )
    return items


def _text(node: ET.Element, tag: str) -> str:
    child = node.find(tag)
    return (child.text or "").strip() if child is not None and child.text else ""


def _date(text: str):
    """An RFC 822 date as used by RSS ("Mon, 26 Oct 2026 04:36:57 GMT"), as an aware datetime."""
    if not text:
        return None
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)

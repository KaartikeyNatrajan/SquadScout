"""Reading RSS feeds, cleaning text, tagging topics, near-duplicate suppression."""

from datetime import UTC, datetime, timedelta

import pytest

from fpl_data.news import selection, text, topics
from fpl_data.news.models import Feed, NewsItem
from fpl_data.news.rss import FeedError, parse_rss

GOOGLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>x</title>
<item><title>Haaland doubt for derby - The Athletic</title>
<link>https://news.google.com/rss/articles/abc</link>
<pubDate>Mon, 26 Oct 2026 04:36:57 GMT</pubDate>
<description>&lt;a href="https://x"&gt;Haaland doubt for derby&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font&gt;The Athletic&lt;/font&gt;</description>
<source url="https://theathletic.com">The Athletic</source></item>
<item><title>No date</title><link>https://x/1</link></item>
<item><title>Bad date</title><link>https://x/2</link><pubDate>yesterday-ish</pubDate></item>
<item><link>https://x/3</link><pubDate>Mon, 26 Oct 2026 04:36:57 GMT</pubDate></item>
</channel></rss>"""

BBC = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title><![CDATA[Saka out for three weeks]]></title>
<description><![CDATA[Arsenal winger Bukayo Saka will miss the next three games. Arteta gave the news on Friday.]]></description>
<link>https://www.bbc.co.uk/sport/1</link><pubDate>Fri, 24 Oct 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_google_items_are_read_and_bad_ones_skipped():
    items = parse_rss(GOOGLE, Feed("g", "u", has_summary=False))

    assert len(items) == 1  # no date, an unreadable date and no title are all skipped
    item = items[0]
    assert item.title == "Haaland doubt for derby"  # " - The Athletic" removed
    assert item.source == "The Athletic"
    assert item.published == datetime(2026, 10, 26, 4, 36, 57, tzinfo=UTC)
    assert item.description == ""  # Google's description just repeats the headline


def test_publisher_items_keep_their_summary_and_get_the_feeds_source():
    [item] = parse_rss(BBC, Feed("BBC Sport", "u", source="BBC Sport"))

    assert item.title == "Saka out for three weeks"
    assert item.source == "BBC Sport"
    assert "Bukayo Saka will miss" in item.description


def test_invalid_xml_is_a_feed_error():
    with pytest.raises(FeedError, match="not valid XML"):
        parse_rss("<html>captcha", Feed("g", "u"))


def test_oversized_feed_is_refused():
    with pytest.raises(FeedError, match="more than"):
        parse_rss("x" * 3_000_001, Feed("g", "u"))


def test_clean_html_removes_tags_entities_and_boilerplate():
    raw = "<p>Saka &amp; Odegaard are fit.</p><p>Continue reading...</p>"
    assert text.clean_html(raw) == "Saka & Odegaard are fit."


def test_title_key_ignores_case_punctuation_and_accents():
    assert text.title_key("Ødegaard: INJURY update!") == text.title_key("odegaard injury update")


def test_snippet_is_the_headline_when_there_is_no_summary():
    assert text.build_snippet("Haaland doubt for derby.", "", []) == "Haaland doubt for derby"


def test_snippet_adds_the_sentence_that_mentions_the_focus_words():
    summary = (
        "The match kicks off at 3pm. Saka will miss the next three games. Tickets are sold out."
    )
    snippet = text.build_snippet("Arsenal news", summary, ["saka", "miss"])
    assert snippet == "Arsenal news. Saka will miss the next three games."


def test_snippet_skips_a_summary_that_repeats_the_headline():
    snippet = text.build_snippet("Saka out for three weeks", "Saka out for three weeks.", ["saka"])
    assert snippet == "Saka out for three weeks"


def test_snippet_is_cut_at_a_word_boundary_within_the_limit():
    snippet = text.build_snippet("Headline", "word " * 100, ["word"])
    assert len(snippet) <= text.SNIPPET_LENGTH
    assert snippet.endswith("…")
    assert not snippet.endswith(" …")


@pytest.mark.parametrize(
    ("headline", "expected"),
    [
        ("Saka ruled out with hamstring injury", "injury"),
        ("Haaland a doubt for the derby", "fitness"),
        ("Rodri banned for three games after red card", "suspension"),
        ("Guardiola to rotate squad, Haaland rested", "rest"),
        ("Arsenal predicted line-up vs Everton", "team_news"),
        ("Maresca press conference: Haaland needs a rest", "press_conference"),
        ("Isak returns to training", "return"),
    ],
)
def test_topics_found(headline, expected):
    assert expected in topics.tag(headline)[0]


@pytest.mark.parametrize(
    "headline",
    [
        "Chelsea 2-0 Spurs: match report",
        "Arsenal sign striker in £60m deal",
        "Critics cast doubt over Howe's Man Utd links",
        "The rest of the season looks bright",
    ],
)
def test_no_topic_for_other_stories(headline):
    assert topics.tag(headline)[0] == []


def _item(title, minutes_ago):
    now = datetime(2026, 10, 26, 12, tzinfo=UTC)
    return NewsItem(title, title, title, "s", "u", now - timedelta(minutes=minutes_ago), [])


def test_near_duplicate_headlines_are_shown_once_newest_first():
    items = [
        _item("Semenyo injury update: out until December, Maresca says", 120),
        _item("Semenyo out until December, Maresca says", 30),
        _item("Saka scan worry ahead of Everton", 60),
    ]

    chosen = selection.select(items, limit=5)

    assert [i.title for i in chosen] == [
        "Semenyo out until December, Maresca says",
        "Saka scan worry ahead of Everton",
    ]


def test_select_respects_the_limit():
    items = [_item(f"Different story number {n} about {'x' * n}", n) for n in range(1, 6)]
    assert len(selection.select(items, limit=2)) == 2

"""Raw feed items -> stories: keeping, merging and the snippet."""

from datetime import UTC, datetime
from types import SimpleNamespace

from fpl_data.news import pipeline
from fpl_data.news.matching import Matcher
from fpl_data.news.models import Feed, RawItem

ARS = 1
PLAYERS = [
    SimpleNamespace(id=1, web_name="Saka", first_name="Bukayo", second_name="Saka", team_id=ARS),
    SimpleNamespace(id=2, web_name="White", first_name="Ben", second_name="White", team_id=ARS),
]
TEAMS = [SimpleNamespace(id=ARS, name="Arsenal", short_name="ARS")]
MATCHER = Matcher(PLAYERS, TEAMS)
WHEN = datetime(2026, 10, 26, tzinfo=UTC)


def raw(title, description="", team_id=None, source="BBC Sport"):
    return RawItem(
        title, "https://x", WHEN, description, source, Feed(source, "u", team_id=team_id)
    )


def test_the_same_headline_from_a_team_feed_adds_what_it_could_place():
    # "White" is an everyday word: on BBC (no club in view) it can't be placed; the
    # same headline from Arsenal's search feed can. The story is kept once, with both.
    headline = "Saka and White ruled out with knee injuries"
    stories = pipeline.process(
        [raw(headline), raw(headline, team_id=ARS, source="Google")], MATCHER
    )

    assert len(stories) == 1
    story = stories[0]
    assert (story.player_ids, story.team_ids, story.source) == ([1, 2], [ARS], "BBC Sport")


def test_the_snippet_prefers_the_sentence_about_the_player():
    summary = "Arsenal have injury worries before the derby. Saka is expected to start on Sunday."
    [story] = pipeline.process([raw("Arsenal team news", summary)], MATCHER)

    assert story.snippet == "Arsenal team news. Saka is expected to start on Sunday."


def test_stories_without_a_topic_or_a_subject_are_dropped():
    stories = pipeline.process(
        [
            raw("Arsenal 2-0 Everton: report"),
            raw("Injury crisis in Serie A"),
            raw("Women's team news: Arsenal"),
        ],
        MATCHER,
    )
    assert stories == []

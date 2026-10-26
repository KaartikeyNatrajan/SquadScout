"""Saving news, the 3-day retention, the hourly refresh and the news RPCs."""

from datetime import UTC, datetime, timedelta

import httpx

from fpl_data.db import news, sync_log
from fpl_data.news.models import NewsItem
from fpl_data.news.refresh import NewsRefresher
from squadscout.v1 import fpl_pb2 as pb
from tests.helpers import count_rows, error_of

SAKA = 12  # Arsenal (team 1)
NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)


def story(title, hours_ago=1.0, players=(SAKA,), teams=(1,), topics=("injury",), key=None):
    return NewsItem(
        dedupe_key=key or title.lower(),
        title=title,
        snippet=f"{title}. More.",
        source="BBC Sport",
        url=f"https://x/{title}",
        published=NOW - timedelta(hours=hours_ago),
        topics=list(topics),
        player_ids=list(players),
        team_ids=list(teams),
    )


def test_save_stores_the_snippet_and_links(seeded_store):
    added = news.save(seeded_store, [story("Saka out")], now=NOW)

    assert added == 1
    [stored] = news.for_player(seeded_store, SAKA, NOW - timedelta(days=3))
    assert (stored.title, stored.snippet, stored.source) == (
        "Saka out",
        "Saka out. More.",
        "BBC Sport",
    )
    assert stored.topics == ["injury"] and stored.team_ids == [1] and stored.player_ids == [SAKA]
    assert sync_log.last_synced(seeded_store, news.DATASET) is not None


def test_the_same_story_is_stored_once(seeded_store):
    news.save(seeded_store, [story("Saka out")], now=NOW)
    added = news.save(seeded_store, [story("Saka out")], now=NOW)

    assert added == 0
    assert count_rows(seeded_store, "news_items") == 1


def test_stories_older_than_three_days_are_not_saved_and_old_ones_are_deleted(seeded_store):
    news.save(seeded_store, [story("Fresh", hours_ago=71), story("Stale", hours_ago=80)], now=NOW)
    assert count_rows(seeded_store, "news_items") == 1  # "Stale" was never saved

    later = NOW + timedelta(hours=2)  # now "Fresh" is 73 hours old
    news.save(seeded_store, [], now=later)

    assert count_rows(seeded_store, "news_items") == 0
    assert count_rows(seeded_store, "news_players") == 0
    assert count_rows(seeded_store, "news_teams") == 0


def test_queries_are_newest_first_and_respect_the_window(seeded_store):
    news.save(
        seeded_store,
        [
            story("Older", hours_ago=30),
            story("Newest", hours_ago=2),
            story("Other club", teams=(2,), players=(13,)),
        ],
        now=NOW,
    )

    by_player = news.for_player(seeded_store, SAKA, NOW - timedelta(hours=72))
    assert [s.title for s in by_player] == ["Newest", "Older"]
    assert [s.title for s in news.for_player(seeded_store, SAKA, NOW - timedelta(hours=6))] == [
        "Newest"
    ]
    assert [s.title for s in news.for_team(seeded_store, 2, NOW - timedelta(hours=72))] == [
        "Other club"
    ]
    assert news.counts_by_player(seeded_store, NOW - timedelta(hours=72)) == {SAKA: 2, 13: 1}


# ----- the hourly refresh -----

RSS = """<rss version="2.0"><channel>
<item><title>Bukayo Saka ruled out with hamstring injury</title><link>https://x/1</link>
<pubDate>{date}</pubDate><description>Arsenal's Saka will miss three games.</description></item>
<item><title>Arsenal 2-0 Everton: match report</title><link>https://x/2</link>
<pubDate>{date}</pubDate></item>
</channel></rss>"""


def rss_transport(requests, fail=()):
    date = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")

    def handler(request):
        requests.append(str(request.url))
        if any(f in str(request.url) for f in fail):
            return httpx.Response(500)
        return httpx.Response(200, text=RSS.format(date=date))

    return httpx.MockTransport(handler)


async def test_refresh_keeps_matching_stories_and_survives_a_failing_feed(seeded_store):
    requests: list[str] = []
    async with httpx.AsyncClient(transport=rss_transport(requests, fail=["skysports"])) as http:
        added = await NewsRefresher(seeded_store, http).refresh()

    assert (
        added == 1
    )  # one story (the match report has no topic); every feed carried it, once stored
    assert any(
        "skysports" in r for r in requests
    )  # the failing feed was tried, and didn't stop the rest
    [stored] = news.for_player(seeded_store, SAKA, datetime.now(UTC) - timedelta(days=3))
    assert "hamstring" in stored.title
    assert "miss three games" in stored.snippet  # BBC's summary sentence is part of the snippet


async def test_refresh_does_nothing_within_the_hour_unless_forced(seeded_store):
    requests: list[str] = []
    async with httpx.AsyncClient(transport=rss_transport(requests)) as http:
        refresher = NewsRefresher(seeded_store, http)
        await refresher.refresh()
        calls = len(requests)

        assert await refresher.refresh() == 0
        assert len(requests) == calls

        await refresher.refresh(force=True)
        assert len(requests) == 2 * calls


async def test_refresh_without_fpl_data_does_nothing(store):
    requests: list[str] = []
    async with httpx.AsyncClient(transport=rss_transport(requests)) as http:
        assert await NewsRefresher(store, http).refresh() == 0
    assert requests == []


# ----- the RPCs -----


async def test_get_player_news(service):
    now = datetime.now(UTC)
    news.save(
        service.store,
        [
            NewsItem(
                "a",
                "Saka injury latest",
                "Saka injury latest",
                "BBC Sport",
                "https://x/a",
                now - timedelta(hours=3),
                ["injury"],
                [SAKA, 13],
                [1],
            ),
            NewsItem(
                "b",
                "Saka injury latest: Arteta",
                "Saka injury latest: Arteta",
                "Sky",
                "https://x/b",
                now - timedelta(hours=4),
                ["injury"],
                [SAKA],
                [1],
            ),
            NewsItem(
                "c",
                "Saka returns to training",
                "Saka returns to training",
                "Guardian",
                "https://x/c",
                now - timedelta(hours=50),
                ["return"],
                [SAKA],
                [1],
            ),
        ],
    )

    response = await service.get_player_news(pb.GetPlayerNewsRequest(player_id=SAKA))

    assert response.player.id == SAKA
    # "b" nearly repeats "a", so it is left out; "c" is older.
    assert [i.title for i in response.items] == ["Saka injury latest", "Saka returns to training"]
    first = response.items[0]
    assert first.age == "3 hours ago" and list(first.topics) == ["injury"]
    assert len(first.also_mentions) == 1  # player 13, named in the same story
    assert "near-duplicates" in response.note


async def test_get_player_news_has_a_note_when_there_is_none(service):
    response = await service.get_player_news(pb.GetPlayerNewsRequest(player_id=SAKA, hours=24))

    assert list(response.items) == []
    assert response.note == "no press stories found in the last 24 hours"


async def test_get_player_news_shows_fpls_own_news_beside_the_press(service):
    # Whatever FPL says is passed through untouched.
    from fpl_data.db import players

    expected = players.get_player(service.store, SAKA).news
    response = await service.get_player_news(pb.GetPlayerNewsRequest(player_id=SAKA))
    assert response.fpl_news == expected


async def test_get_player_news_unknown_player(service):
    err = await error_of(service.get_player_news(pb.GetPlayerNewsRequest(player_id=99999)))
    assert "SearchPlayers" in err.message


async def test_get_team_news_by_name(service):
    now = datetime.now(UTC)
    news.save(
        service.store,
        [
            NewsItem(
                "a",
                "Arsenal team news",
                "Arsenal team news",
                "Sky",
                "https://x/a",
                now - timedelta(hours=1),
                ["team_news"],
                [],
                [1],
            )
        ],
    )

    response = await service.get_team_news(pb.GetTeamNewsRequest(team_name="Arsenal"))

    assert (response.team_id, response.team_name) == (1, "Arsenal")
    assert [i.title for i in response.items] == ["Arsenal team news"]

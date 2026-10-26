"""Which feeds to read: a few publishers, and a Google News search per team."""

from collections.abc import Sequence
from urllib.parse import quote_plus

from fpl_data.db.teams import TeamRow
from fpl_data.news.models import Feed

PUBLISHER_FEEDS = [
    Feed(
        "BBC Sport",
        "https://feeds.bbci.co.uk/sport/football/premier-league/rss.xml",
        source="BBC Sport",
    ),
    Feed(
        "The Guardian",
        "https://www.theguardian.com/football/premierleague/rss",
        source="The Guardian",
    ),
    Feed("Sky Sports", "https://www.skysports.com/rss/11661", source="Sky Sports"),
]

# FPL's short club names are not what newspapers use. Teams not listed here are
# searched for under their FPL name.
TEAM_SEARCH_NAMES = {
    "Man City": "Manchester City",
    "Man Utd": "Manchester United",
    "Spurs": "Tottenham Hotspur",
    "Nott'm Forest": "Nottingham Forest",
    "Leeds": "Leeds United",
    "Newcastle": "Newcastle United",
    "Wolves": "Wolverhampton Wanderers",
}

# Two searches per team: one for injuries, one for team news (line-ups, rotation,
# press conferences). "when:3d" limits results to the last three days.
_SEARCHES = [("injury", "injury"), ("team news", "team news")]


def google_news_feed(query: str, name: str, team_id: int | None = None) -> Feed:
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-GB&gl=GB&ceid=GB:en"
    # Google News items name their outlet themselves, and carry only a headline.
    return Feed(name, url, has_summary=False, team_id=team_id)


def team_feeds(teams: Sequence[TeamRow]) -> list[Feed]:
    """The Google News searches for every team."""
    feeds = []
    for team in teams:
        search_name = TEAM_SEARCH_NAMES.get(team.name, team.name)
        for label, words in _SEARCHES:
            feeds.append(
                google_news_feed(
                    f"{search_name} {words} when:3d", f"Google News: {team.name} {label}", team.id
                )
            )
    return feeds


def all_feeds(teams: Sequence[TeamRow]) -> list[Feed]:
    return PUBLISHER_FEEDS + team_feeds(teams)

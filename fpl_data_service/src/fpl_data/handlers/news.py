"""GetPlayerNews and GetTeamNews."""

from datetime import UTC, datetime, timedelta

from fpl_data.db import news, players, teams
from fpl_data.handlers import convert
from fpl_data.handlers.base import ServiceBase, clamp_limit
from fpl_data.news import selection
from fpl_data.news.models import NewsItem
from squadscout.v1 import fpl_pb2 as pb

DEFAULT_PLAYER_ITEMS = 3
DEFAULT_TEAM_ITEMS = 5
MAX_ITEMS = 10
MAX_HOURS = int(news.RETENTION.total_seconds() // 3600)  # 72


class NewsHandlers(ServiceBase):
    async def get_player_news(self, request: pb.GetPlayerNewsRequest) -> pb.GetPlayerNewsResponse:
        """Press stories about a player, next to FPL's own news for him. The two are
        shown side by side, never merged: FPL's is official but slow, the press is quick
        but may be wrong, and the caller should be able to tell which said what."""
        player = self.lookup_player(request.player_id)
        window = _window(request.hours)
        limit = clamp_limit(request.limit or DEFAULT_PLAYER_ITEMS, MAX_ITEMS)

        stories = news.for_player(self.store, player.id, datetime.now(UTC) - window)
        chosen = selection.select(_news_items(stories), limit)
        by_id = {str(s.id): s for s in stories}
        names = _names(self.store, {pid for s in stories for pid in s.player_ids})
        return pb.GetPlayerNewsResponse(
            player=_player_pb(self, player),
            fpl_news=player.news,
            fpl_news_added=player.news_added,
            items=[_to_pb(by_id[i.dedupe_key], names, exclude=player.id) for i in chosen],
            note=_note(len(chosen), len(stories), window),
        )

    async def get_team_news(self, request: pb.GetTeamNewsRequest) -> pb.GetTeamNewsResponse:
        team_id = self._resolve_team(request.team_id, request.team_name)
        team = next(t for t in teams.all_teams(self.store) if t.id == team_id)
        window = _window(request.hours)
        limit = clamp_limit(request.limit or DEFAULT_TEAM_ITEMS, MAX_ITEMS)

        stories = news.for_team(self.store, team_id, datetime.now(UTC) - window)
        chosen = selection.select(_news_items(stories), limit)
        by_id = {str(s.id): s for s in stories}
        names = _names(self.store, {pid for s in stories for pid in s.player_ids})
        return pb.GetTeamNewsResponse(
            team_id=team.id,
            team_name=team.name,
            items=[_to_pb(by_id[i.dedupe_key], names, exclude=None) for i in chosen],
            note=_note(len(chosen), len(stories), window),
        )


def _window(hours: int) -> timedelta:
    if hours <= 0 or hours > MAX_HOURS:
        hours = MAX_HOURS
    return timedelta(hours=hours)


def _news_items(stories: list[news.StoredNews]):
    """StoredNews as the pipeline's NewsItem, which selection works on."""
    # The story's id stands in for the dedupe key, which isn't read back from the database.
    return [
        NewsItem(str(s.id), s.title, s.snippet, s.source, s.url, s.published, s.topics)
        for s in stories
    ]


def _names(store, ids: set[int]) -> dict[int, str]:
    return {pid: p.web_name for pid, p in players.players_by_id(store, sorted(ids)).items()}


def _player_pb(service: ServiceBase, player) -> pb.Player:
    return convert.to_player(player, service.recent_transfers().get(player.id))


def _to_pb(story: news.StoredNews, names: dict[int, str], exclude: int | None) -> pb.NewsItem:
    return pb.NewsItem(
        title=story.title,
        snippet=story.snippet,
        source=story.source,
        published_at=story.published.strftime("%Y-%m-%dT%H:%M:%SZ"),
        age=_age(datetime.now(UTC) - story.published),
        url=story.url,
        topics=story.topics,
        also_mentions=[names[pid] for pid in story.player_ids if pid != exclude and pid in names],
    )


def _age(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return "just now" if minutes < 2 else f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    return f"{hours // 24} days ago"


def _note(shown: int, found: int, window: timedelta) -> str:
    hours = int(window.total_seconds() // 3600)
    if found == 0:
        return f"no press stories found in the last {hours} hours"
    if shown < found:
        return f"{shown} of {found} stories shown; the rest were near-duplicates or older"
    return ""

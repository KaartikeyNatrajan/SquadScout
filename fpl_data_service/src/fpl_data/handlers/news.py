"""GetPlayerNews and GetTeamNews: press stories saved by the hourly news job."""

from datetime import UTC, datetime, timedelta

from fpl_data.db import news, players
from fpl_data.db.news import StoredNews
from fpl_data.handlers import convert
from fpl_data.handlers.base import ServiceBase, clamp_limit
from fpl_data.news import selection
from squadscout.v1 import fpl_pb2 as pb

DEFAULT_PLAYER_ITEMS = 3
DEFAULT_TEAM_ITEMS = 5
MAX_ITEMS = 10
MAX_HOURS = int(news.RETENTION.total_seconds() // 3600)  # 72: nothing older is kept


class NewsHandlers(ServiceBase):
    async def get_player_news(self, request: pb.GetPlayerNewsRequest) -> pb.GetPlayerNewsResponse:
        """Press stories about a player, next to FPL's own news for him. The two are
        shown side by side, never merged: FPL's is official but slow, the press is quick
        but may be wrong, and the caller should be able to tell which said what."""
        player = self.lookup_player(request.player_id)
        window = _window(request.hours)
        limit = clamp_limit(request.limit or DEFAULT_PLAYER_ITEMS, MAX_ITEMS)

        stories = news.for_player(self.store, player.id, datetime.now(UTC) - window)
        chosen = selection.select(stories, limit)
        return pb.GetPlayerNewsResponse(
            player=convert.to_player(player, self.recent_transfers().get(player.id)),
            fpl_news=player.news,
            fpl_news_added=player.news_added,
            items=self._items(chosen, about_player=player.id),
            note=_note(len(chosen), len(stories), window),
        )

    async def get_team_news(self, request: pb.GetTeamNewsRequest) -> pb.GetTeamNewsResponse:
        """Press stories about a club, by team id or name."""
        team = self.lookup_team(request.team_id, request.team_name)
        window = _window(request.hours)
        limit = clamp_limit(request.limit or DEFAULT_TEAM_ITEMS, MAX_ITEMS)

        stories = news.for_team(self.store, team.id, datetime.now(UTC) - window)
        chosen = selection.select(stories, limit)
        return pb.GetTeamNewsResponse(
            team_id=team.id,
            team_name=team.name,
            items=self._items(chosen),
            note=_note(len(chosen), len(stories), window),
        )

    def _items(
        self, stories: list[StoredNews], about_player: int | None = None
    ) -> list[pb.NewsItem]:
        """The stories as protobuf. `also_mentions` lists the other players each names
        (everyone, for a club's news), so the reader knows a headline may be about someone else."""
        ids = sorted({pid for s in stories for pid in s.player_ids})
        web_names = {pid: p.web_name for pid, p in players.players_by_id(self.store, ids).items()}
        now = datetime.now(UTC)
        return [
            pb.NewsItem(
                title=s.title,
                snippet=s.snippet,
                source=s.source,
                published_at=s.published.strftime("%Y-%m-%dT%H:%M:%SZ"),
                age=_age(now - s.published),
                url=s.url,
                topics=s.topics,
                also_mentions=[
                    web_names[pid]
                    for pid in s.player_ids
                    if pid != about_player and pid in web_names
                ],
            )
            for s in stories
        ]


def _window(hours: int) -> timedelta:
    """How far back to look: `hours`, or everything kept (72 h) if 0 or too many."""
    if hours <= 0 or hours > MAX_HOURS:
        hours = MAX_HOURS
    return timedelta(hours=hours)


def _age(delta: timedelta) -> str:
    """ "3 hours ago": easier for a model than working out a timestamp's age itself."""
    minutes = int(delta.total_seconds() // 60)
    if minutes < 2:
        return "just now"
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    return f"{hours // 24} days ago"


def _note(shown: int, found: int, window: timedelta) -> str:
    """A plain-word remark on coverage, so an empty list isn't mistaken for "no news is good news"."""
    hours = int(window.total_seconds() // 3600)
    if found == 0:
        return f"no press stories found in the last {hours} hours"
    if shown < found:
        return f"{shown} of {found} stories shown; the rest were near-duplicates or older"
    return ""

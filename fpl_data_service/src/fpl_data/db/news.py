"""News stories saved from the RSS feeds (see news/)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fpl_data.db import sync_log
from fpl_data.db.store import Store
from fpl_data.news.models import NewsItem

DATASET = "news"
RETENTION = timedelta(days=3)  # stories older than this are deleted


@dataclass
class StoredNews:
    id: int
    title: str
    snippet: str
    source: str
    url: str
    published: datetime
    topics: list[str]
    player_ids: list[int]
    team_ids: list[int]


def _format(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def save(store: Store, items: Sequence[NewsItem], now: datetime | None = None) -> int:
    """Save new stories (ones already stored are left alone), delete stories older than
    RETENTION, and record the sync. Returns how many stories were new."""
    now = now or datetime.now(UTC)
    added = 0
    with store.transaction() as conn:
        for item in items:
            if now - item.published > RETENTION:
                continue
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO news_items
                    (dedupe_key, title, snippet, source, url, published_at, fetched_at, topics)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.dedupe_key,
                    item.title,
                    item.snippet,
                    item.source,
                    item.url,
                    _format(item.published),
                    _format(now),
                    ",".join(item.topics),
                ),
            )
            if cursor.rowcount == 0:
                continue
            added += 1
            news_id = cursor.lastrowid
            conn.executemany(
                "INSERT OR IGNORE INTO news_players (news_id, player_id) VALUES (?, ?)",
                [(news_id, pid) for pid in item.player_ids],
            )
            conn.executemany(
                "INSERT OR IGNORE INTO news_teams (news_id, team_id) VALUES (?, ?)",
                [(news_id, tid) for tid in item.team_ids],
            )
        _delete_older_than(conn, _format(now - RETENTION))
        sync_log.record_sync(conn, DATASET)
    return added


def _delete_older_than(conn, cutoff: str) -> None:
    old = "SELECT id FROM news_items WHERE published_at < ?"
    conn.execute(f"DELETE FROM news_players WHERE news_id IN ({old})", (cutoff,))
    conn.execute(f"DELETE FROM news_teams WHERE news_id IN ({old})", (cutoff,))
    conn.execute("DELETE FROM news_items WHERE published_at < ?", (cutoff,))


def for_player(store: Store, player_id: int, since: datetime) -> list[StoredNews]:
    return _load(
        store, "JOIN news_players m ON m.news_id = n.id WHERE m.player_id = ?", player_id, since
    )


def for_team(store: Store, team_id: int, since: datetime) -> list[StoredNews]:
    return _load(store, "JOIN news_teams m ON m.news_id = n.id WHERE m.team_id = ?", team_id, since)


def _load(store: Store, join_where: str, key: int, since: datetime) -> list[StoredNews]:
    rows = store.conn.execute(
        f"""
        SELECT n.* FROM news_items n {join_where} AND n.published_at >= ?
        ORDER BY n.published_at DESC, n.id""",
        (key, _format(since)),
    ).fetchall()
    return [_stored(store, r) for r in rows]


def _stored(store: Store, row) -> StoredNews:
    players = [
        r[0]
        for r in store.conn.execute(
            "SELECT player_id FROM news_players WHERE news_id = ? ORDER BY player_id", (row["id"],)
        )
    ]
    teams = [
        r[0]
        for r in store.conn.execute(
            "SELECT team_id FROM news_teams WHERE news_id = ? ORDER BY team_id", (row["id"],)
        )
    ]
    return StoredNews(
        id=row["id"],
        title=row["title"],
        snippet=row["snippet"],
        source=row["source"],
        url=row["url"],
        published=datetime.fromisoformat(row["published_at"]),
        topics=[t for t in row["topics"].split(",") if t],
        player_ids=players,
        team_ids=teams,
    )


def counts_by_player(store: Store, since: datetime) -> dict[int, int]:
    """How many stored stories mention each player (published since `since`)."""
    rows = store.conn.execute(
        """
        SELECT m.player_id, COUNT(*) AS n FROM news_players m
        JOIN news_items i ON i.id = m.news_id
        WHERE i.published_at >= ? GROUP BY m.player_id""",
        (_format(since),),
    )
    return {r["player_id"]: r["n"] for r in rows}

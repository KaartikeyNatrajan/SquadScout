"""Tracking when each dataset was last downloaded (cache freshness)."""

import sqlite3
from datetime import UTC, datetime

from fpl_data.db.store import Store

# Dataset names used as keys in sync_log. Each one is one download from the
# FPL API; refresh.py uses them to decide what is stale.
BOOTSTRAP = "bootstrap"
FIXTURES = "fixtures"


def gameweek_stats_dataset(gameweek: int) -> str:
    """The key for every player's stats in one gameweek. Each gameweek is
    downloaded (and goes stale) separately."""
    return f"event-live/{gameweek}"


def last_synced(store: Store, dataset: str) -> datetime | None:
    """When `dataset` was last downloaded, or None if it never has been."""
    row = store.conn.execute(
        "SELECT synced_at FROM sync_log WHERE dataset = ?", (dataset,)
    ).fetchone()
    if row is None:
        return None
    return datetime.fromisoformat(row["synced_at"])


def record_sync(conn: sqlite3.Connection, dataset: str) -> None:
    """Mark `dataset` as downloaded now.

    It takes the caller's connection, called inside the caller's transaction,
    so the sync is only recorded if the data itself saves too: sync_log can
    never claim a download that didn't make it into the tables.
    """
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """
        INSERT INTO sync_log (dataset, synced_at) VALUES (?, ?)
        ON CONFLICT(dataset) DO UPDATE SET synced_at = excluded.synced_at""",
        (dataset, now),
    )

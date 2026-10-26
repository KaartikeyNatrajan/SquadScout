"""Gameweeks ("events" in FPL) and chip allowances."""

from dataclasses import dataclass

from fpl_data.db.store import Store


@dataclass
class GameweekRow:
    id: int  # 1-38
    deadline: str  # RFC 3339: transfers and team changes lock at this time


def next_gameweek(store: Store) -> GameweekRow | None:
    """The gameweek whose deadline hasn't passed yet, or None once the season is over."""
    row = store.conn.execute("SELECT id, deadline_time FROM events WHERE is_next = 1").fetchone()
    return GameweekRow(row["id"], row["deadline_time"]) if row else None


def current_gameweek(store: Store) -> int | None:
    """The gameweek whose deadline passed most recently (being played now, or just
    played), or None before the season starts."""
    row = store.conn.execute("SELECT id FROM events WHERE is_current = 1").fetchone()
    return row["id"] if row else None


def deadline(store: Store, gameweek: int) -> str | None:
    """The transfer deadline of a gameweek (RFC 3339), or None if there's no such gameweek."""
    row = store.conn.execute(
        "SELECT deadline_time FROM events WHERE id = ?", (gameweek,)
    ).fetchone()
    return row["deadline_time"] if row else None


@dataclass
class ChipRow:
    """A chip allowance: the chip may be played once between start_event and stop_event (inclusive)."""

    name: str  # "wildcard", "freehit", "bboost", "3xc"
    start_event: int
    stop_event: int


def chips(store: Store) -> list[ChipRow]:
    """Every chip allowance this season, in id order."""
    rows = store.conn.execute("SELECT name, start_event, stop_event FROM chips ORDER BY id")
    return [ChipRow(r["name"], r["start_event"], r["stop_event"]) for r in rows]

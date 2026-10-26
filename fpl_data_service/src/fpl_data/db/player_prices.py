"""Players' price in each gameweek (from /element-summary/{id}/)."""

from collections.abc import Iterable

from fpl_data.db.store import Store


def save_prices(store: Store, player_id: int, prices: Iterable[tuple[int, int]]) -> None:
    """Save a player's price history, given as (gameweek, price in tenths of £m) pairs.
    Saving a player again simply replaces his rows."""
    with store.transaction() as conn:
        conn.execute("DELETE FROM player_prices WHERE player_id = ?", (player_id,))
        conn.executemany(
            "INSERT INTO player_prices (player_id, gameweek, price) VALUES (?, ?, ?)",
            [(player_id, gameweek, price) for gameweek, price in prices],
        )


def has_prices(store: Store, player_id: int) -> bool:
    """Whether this player's price history has been downloaded."""
    row = store.conn.execute(
        "SELECT 1 FROM player_prices WHERE player_id = ? LIMIT 1", (player_id,)
    ).fetchone()
    return row is not None


def price_at(store: Store, player_id: int, gameweek: int) -> int | None:
    """The player's price (tenths of £m) in `gameweek`, or None if we don't know it."""
    row = store.conn.execute(
        "SELECT price FROM player_prices WHERE player_id = ? AND gameweek = ?",
        (player_id, gameweek),
    ).fetchone()
    return row["price"] if row else None

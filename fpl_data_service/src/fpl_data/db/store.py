"""Opening the database, schema versioning and transactions."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

# Must be increased whenever schema.sql changes. See _reset_if_outdated.
SCHEMA_VERSION = 6

# Every table this service has ever created, children before parents, so
# dropping them in this order never breaks a REFERENCES rule. Keep old names
# here after renaming a table, so old databases get cleaned up.
_ALL_TABLES = [
    "player_gameweek_stats",  # version 1
    "player_match_stats",  # versions 2-3
    "player_prices",
    "transfer_snapshots",
    "player_gameweek_matches",
    "player_gameweeks",
    "manager_teams",
    "fixtures",
    "players",
    "events",
    "teams",
    "chips",
    "sync_log",
]


class Store:
    """The database connection. Every query lives in the db modules, so the
    rest of the code never writes raw SQL.

    All access happens on the event loop's single thread, with no `await`
    between the start and end of a database operation, so one connection is
    enough and operations can never interleave.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @classmethod
    def open(cls, path: str | Path) -> "Store":
        """Create the database file if needed, connect, and create the tables."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # isolation_level=None turns off Python's own transaction handling, so
        # `transaction()` below decides exactly when BEGIN and COMMIT happen.
        # Row objects let queries read columns by name: row["web_name"].
        conn = sqlite3.connect(path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        # SQLite options ("pragmas"):
        #   journal_mode=WAL   readers aren't blocked while a refresh writes
        #   foreign_keys=ON    enforce the REFERENCES rules in schema.sql
        #   busy_timeout       wait up to 5 s for a lock instead of failing at once
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")

        _reset_if_outdated(conn)
        # Run every CREATE TABLE IF NOT EXISTS statement in schema.sql.
        conn.executescript(_SCHEMA)
        # Remember which schema these tables were made from. (PRAGMA values
        # can't be ? parameters; SCHEMA_VERSION is our own constant, so this is safe.)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return cls(conn)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """All-or-nothing: everything inside the `with` block is saved together,
        or (if it raises) none of it is. Don't `await` inside the block.

            with store.transaction() as conn:
                conn.execute(...)
        """
        self.conn.execute("BEGIN")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")


def _reset_if_outdated(conn: sqlite3.Connection) -> None:
    """Drop every table if the database was made from an older schema.sql.

    SQLite keeps a free integer in each database file, "user_version", for
    exactly this purpose. It starts at 0, and `Store.open` sets it to
    SCHEMA_VERSION. Dropping is fine because this database is only a cache:
    the refresh job downloads everything again.
    """
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    if version == SCHEMA_VERSION:
        return
    for table in _ALL_TABLES:
        # Table names can't be ? parameters; safe because they're our own constants.
        conn.execute(f"DROP TABLE IF EXISTS {table}")


def placeholders(count: int) -> str:
    """ "?,?,?" for `count` values, for `IN (...)` lists. The values themselves
    are still passed as parameters, never pasted into the SQL text."""
    return ",".join("?" * count)

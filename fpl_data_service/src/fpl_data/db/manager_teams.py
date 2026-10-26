"""Managers' teams, saved until the next deadline.

This module stores a team without knowing its shape: the manager package turns
a team into JSON and back. That keeps the import direction one-way (manager
uses db, never the other way round).
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from fpl_data.db.store import Store


@dataclass
class SavedTeam:
    data: str  # JSON, as written by the manager package
    fetched_at: datetime  # when it was downloaded
    valid_until: datetime  # the deadline after which the squad may have changed


def _format(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_manager_team(
    store: Store, team_id: int, data: str, fetched_at: datetime, valid_until: datetime
) -> None:
    """Store a team's JSON, replacing any older copy."""
    with store.transaction() as conn:
        conn.execute(
            """
            INSERT INTO manager_teams (team_id, data, fetched_at, valid_until)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(team_id) DO UPDATE SET
                data = excluded.data,
                fetched_at = excluded.fetched_at,
                valid_until = excluded.valid_until""",
            (team_id, data, _format(fetched_at), _format(valid_until)),
        )


def load_manager_team(store: Store, team_id: int) -> SavedTeam | None:
    """A stored team, fresh or not (the caller decides using valid_until), or
    None if the team was never stored."""
    row = store.conn.execute(
        "SELECT data, fetched_at, valid_until FROM manager_teams WHERE team_id = ?", (team_id,)
    ).fetchone()
    if row is None:
        return None
    return SavedTeam(
        data=row["data"],
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
        valid_until=datetime.fromisoformat(row["valid_until"]),
    )

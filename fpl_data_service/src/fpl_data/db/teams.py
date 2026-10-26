"""Team lookups."""

from dataclasses import dataclass

from fpl_data.db.store import Store


@dataclass
class TeamRow:
    """One Premier League club."""

    id: int
    name: str  # "Arsenal", "Man Utd", "Nott'm Forest"
    short_name: str  # "ARS", "MUN", "NFO"


def all_teams(store: Store) -> list[TeamRow]:
    """All stored teams in id order (20 in a season)."""
    rows = store.conn.execute("SELECT id, name, short_name FROM teams ORDER BY id")
    return [TeamRow(r["id"], r["name"], r["short_name"]) for r in rows]


def team_exists(store: Store, team_id: int) -> bool:
    row = store.conn.execute(
        "SELECT EXISTS (SELECT 1 FROM teams WHERE id = ?)", (team_id,)
    ).fetchone()
    return bool(row[0])

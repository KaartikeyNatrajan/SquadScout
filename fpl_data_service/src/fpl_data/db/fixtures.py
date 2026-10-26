"""Fixtures (from /fixtures/)."""

from collections.abc import Sequence
from dataclasses import dataclass

from fpl_data.db import sync_log
from fpl_data.db.store import Store
from fpl_data.fpl.models import Fixture


@dataclass
class FixtureRow:
    """One fixture with both team names filled in."""

    id: int
    gameweek: int  # 0 if not scheduled yet
    kickoff_time: str  # RFC 3339; "" if not scheduled yet
    home_team_id: int
    home_team_name: str
    away_team_id: int
    away_team_name: str
    home_difficulty: int  # FDR 1 (easy) - 5 (hard), for the home team
    away_difficulty: int
    finished: bool
    home_score: int | None  # None until played
    away_score: int | None


def upsert_fixtures(store: Store, fixtures: Sequence[Fixture]) -> None:
    """Save every fixture from /fixtures/ and record the sync, all-or-nothing.

    Teams must be stored first (upsert_bootstrap): fixtures reference them.
    """
    with store.transaction() as conn:
        conn.executemany(
            """
            INSERT INTO fixtures (
                id, gameweek, home_team_id, away_team_id, kickoff_time,
                home_difficulty, away_difficulty, finished, home_score, away_score
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                gameweek = excluded.gameweek,
                home_team_id = excluded.home_team_id,
                away_team_id = excluded.away_team_id,
                kickoff_time = excluded.kickoff_time,
                home_difficulty = excluded.home_difficulty,
                away_difficulty = excluded.away_difficulty,
                finished = excluded.finished,
                home_score = excluded.home_score,
                away_score = excluded.away_score""",
            # event, kickoff_time and the scores can be None: stored as NULL.
            [
                (
                    f.id,
                    f.event,
                    f.team_h,
                    f.team_a,
                    f.kickoff_time,
                    f.team_h_difficulty,
                    f.team_a_difficulty,
                    f.finished,
                    f.team_h_score,
                    f.team_a_score,
                )
                for f in fixtures
            ],
        )
        sync_log.record_sync(conn, sync_log.FIXTURES)


def upcoming_fixtures(store: Store, team_id: int, limit: int = 0) -> list[FixtureRow]:
    """A team's unfinished fixtures, soonest first. limit <= 0 means all of them."""
    sql_limit = limit if limit > 0 else -1  # SQLite's way of saying "no limit"

    # The fixtures table is joined twice, once per team, to get both names; the
    # aliases h (home) and a (away) tell the two copies apart.
    # COALESCE(x, fallback) turns NULL into the fallback, so unscheduled
    # fixtures fit plain int/str fields.
    # "kickoff_time IS NULL" sorts as 0 for scheduled fixtures and 1 for
    # unscheduled ones, so undated fixtures go last instead of first.
    rows = store.conn.execute(
        """
        SELECT f.id, COALESCE(f.gameweek, 0) AS gameweek, COALESCE(f.kickoff_time, '') AS kickoff_time,
               f.home_team_id, h.name AS home_team_name, f.away_team_id, a.name AS away_team_name,
               f.home_difficulty, f.away_difficulty, f.finished,
               f.home_score, f.away_score
        FROM fixtures f
        JOIN teams h ON h.id = f.home_team_id
        JOIN teams a ON a.id = f.away_team_id
        WHERE (f.home_team_id = ? OR f.away_team_id = ?) AND f.finished = 0
        ORDER BY f.kickoff_time IS NULL, f.kickoff_time, f.id
        LIMIT ?""",
        (team_id, team_id, sql_limit),
    ).fetchall()
    return [FixtureRow(**{**dict(r), "finished": bool(r["finished"])}) for r in rows]

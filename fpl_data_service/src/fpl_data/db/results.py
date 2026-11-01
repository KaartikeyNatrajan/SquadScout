"""Finished matches with their scorers, assists and red cards (see history/)."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from fpl_data.db import sync_log
from fpl_data.db.store import Store, placeholders
from fpl_data.history.results import Club, MatchEvent, Person, Result


def season_dataset(season: str) -> str:
    """The sync_log key for one season's results."""
    return f"results/{season}"


def save_season(store: Store, season: str, results: Sequence[Result]) -> None:
    """Replace one season's results and record the sync, all-or-nothing."""
    with store.transaction() as conn:
        conn.execute("DELETE FROM match_events WHERE season = ?", (season,))
        conn.execute("DELETE FROM match_results WHERE season = ?", (season,))
        conn.executemany(
            """
            INSERT INTO match_results (
                season, fixture_id, kickoff_time, home_team_code, home_team_name,
                away_team_code, away_team_name, home_score, away_score
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (season, r.fixture_id, r.kickoff_time, r.home.code, r.home.name)
                + (r.away.code, r.away.name, r.home_score, r.away_score)
                for r in results
            ],
        )
        conn.executemany(
            """
            INSERT INTO match_events (season, fixture_id, kind, team_code, player_code, player_name, count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO UPDATE SET count = count + excluded.count""",
            [
                (season, r.fixture_id, e.kind, e.team_code, e.player_code, e.player_name, e.count)
                for r in results
                for e in r.events
            ],
        )
        sync_log.record_sync(conn, season_dataset(season))


def has_season(store: Store, season: str) -> bool:
    return sync_log.last_synced(store, season_dataset(season)) is not None


def meetings(
    store: Store, team_code: int, opponent_code: int, seasons: Sequence[str]
) -> list[Result]:
    """The matches between two clubs in `seasons`, newest first, with their events."""
    if not seasons:
        return []
    rows = store.conn.execute(
        f"""
        SELECT * FROM match_results
        WHERE ((home_team_code = ? AND away_team_code = ?) OR (home_team_code = ? AND away_team_code = ?))
          AND season IN ({placeholders(len(seasons))})
        ORDER BY kickoff_time DESC""",
        (team_code, opponent_code, opponent_code, team_code, *seasons),
    ).fetchall()
    results = [
        Result(
            season=r["season"],
            fixture_id=r["fixture_id"],
            kickoff_time=r["kickoff_time"],
            home=Club(r["home_team_code"], r["home_team_name"]),
            away=Club(r["away_team_code"], r["away_team_name"]),
            home_score=r["home_score"],
            away_score=r["away_score"],
        )
        for r in rows
    ]
    for result in results:
        result.events = [
            MatchEvent(e["kind"], e["team_code"], e["player_code"], e["player_name"], e["count"])
            for e in store.conn.execute(
                """
                SELECT * FROM match_events WHERE season = ? AND fixture_id = ?
                ORDER BY CASE kind
                    WHEN 'goal' THEN 1 WHEN 'own_goal' THEN 2 WHEN 'assist' THEN 3
                    WHEN 'penalty_missed' THEN 4 WHEN 'penalty_saved' THEN 5 ELSE 6 END,
                    count DESC, player_name""",
                (result.season, result.fixture_id),
            )
        ]
    return results


def seasons_played(store: Store, team_code: int, seasons: Sequence[str]) -> set[str]:
    """Which of `seasons` the club has results in (i.e. was in the Premier League)."""
    rows = store.conn.execute(
        f"""
        SELECT DISTINCT season FROM match_results
        WHERE (home_team_code = ? OR away_team_code = ?) AND season IN ({placeholders(len(seasons))})""",
        (team_code, team_code, *seasons),
    )
    return {r["season"] for r in rows}


@dataclass
class Lookups:
    """This season's ids -> lasting codes, to read the current season's FPL fixtures."""

    clubs: dict[int, Club] = field(default_factory=dict)
    people: dict[int, Person] = field(default_factory=dict)


def current_lookups(store: Store) -> Lookups:
    clubs = {
        r["id"]: Club(r["code"], r["name"])
        for r in store.conn.execute("SELECT id, code, name FROM teams")
    }
    people = {
        r["id"]: Person(r["code"], r["web_name"])
        for r in store.conn.execute("SELECT id, code, web_name FROM players")
    }
    return Lookups(clubs, people)


def player_ids_by_code(store: Store, codes: Sequence[int]) -> dict[int, int]:
    """Today's player ids for players identified by their lasting code."""
    if not codes:
        return {}
    rows = store.conn.execute(
        f"SELECT id, code FROM players WHERE code IN ({placeholders(len(codes))})", tuple(codes)
    )
    return {r["code"]: r["id"] for r in rows}

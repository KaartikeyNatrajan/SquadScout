"""Players' stats per gameweek (from /event/{gw}/live/).

Two tables: `player_gameweeks` holds FPL's weekly totals, and
`player_gameweek_matches` holds the one or two matches behind each week (FPL
only splits minutes and points per match; everything else is a weekly total).
"""

from dataclasses import dataclass, field

from fpl_data.db import sync_log
from fpl_data.db.store import Store, placeholders
from fpl_data.fpl.models import EventLive


@dataclass
class MatchRow:
    """One match from the player's side."""

    fixture_id: int
    opponent_team_id: int
    opponent_team_name: str
    was_home: bool
    kickoff_time: str
    team_score: int | None  # his club's goals; None if the match hasn't started
    opponent_score: int | None
    minutes: int
    total_points: int


@dataclass
class PlayerGameweekRow:
    """One player's FPL totals for one gameweek, plus the match(es) behind them."""

    player_id: int
    gameweek: int
    minutes: int
    starts: int
    goals_scored: int
    assists: int
    clean_sheets: int
    goals_conceded: int
    saves: int
    bonus: int
    yellow_cards: int
    red_cards: int
    total_points: int
    expected_goals: float
    expected_assists: float
    expected_goal_involvements: float
    matches: list[MatchRow] = field(default_factory=list)  # in kickoff order


def upsert_gameweek_stats(store: Store, gameweek: int, live: EventLive) -> None:
    """Replace every player's stats for one gameweek with `live` (from
    /event/{gw}/live/) and record the sync, all-or-nothing.

    Players and fixtures must be stored first (upsert_bootstrap, upsert_fixtures).

    Delete-then-insert rather than upsert: FPL corrects stats after matches,
    and a correction can remove a row (e.g. a fixture moved to another week).
    Readers never see the gameweek half rewritten: the whole change becomes
    visible only when the transaction commits.
    """
    with store.transaction() as conn:
        # Each player's club right now, to record which side of each fixture he
        # was on. (FPL's live data doesn't say.)
        clubs = {
            row["id"]: row["team_id"] for row in conn.execute("SELECT id, team_id FROM players")
        }

        # Matches first: they reference the gameweek rows.
        conn.execute("DELETE FROM player_gameweek_matches WHERE gameweek = ?", (gameweek,))
        conn.execute("DELETE FROM player_gameweeks WHERE gameweek = ?", (gameweek,))

        week_rows = []
        match_rows = []
        for p in live.elements:
            # No matches: his club had a blank gameweek. Nothing to record.
            if not p.explain:
                continue
            # Not in the players table (FPL removed him since): skip rather than
            # break the foreign key and lose the whole gameweek.
            club = clubs.get(p.id)
            if club is None:
                continue

            st = p.stats
            week_rows.append(
                (
                    p.id,
                    gameweek,
                    st.minutes,
                    st.starts,
                    st.goals_scored,
                    st.assists,
                    st.clean_sheets,
                    st.goals_conceded,
                    st.saves,
                    st.bonus,
                    st.yellow_cards,
                    st.red_cards,
                    st.total_points,
                    st.expected_goals,
                    st.expected_assists,
                    st.expected_goal_involvements,
                )
            )
            for m in p.explain:
                match_rows.append((p.id, gameweek, m.fixture, club, m.minutes(), m.points()))

        conn.executemany(
            """
            INSERT INTO player_gameweeks (
                player_id, gameweek, minutes, starts, goals_scored, assists, clean_sheets,
                goals_conceded, saves, bonus, yellow_cards, red_cards, total_points,
                expected_goals, expected_assists, expected_goal_involvements
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            week_rows,
        )
        conn.executemany(
            """
            INSERT INTO player_gameweek_matches (player_id, gameweek, fixture_id, team_id, minutes, total_points)
            VALUES (?, ?, ?, ?, ?, ?)""",
            match_rows,
        )
        sync_log.record_sync(conn, sync_log.gameweek_stats_dataset(gameweek))


# The SELECT shared by the queries below. Its columns match PlayerGameweekRow's
# fields, so a result row converts with PlayerGameweekRow(**row).
_SELECT = """
    SELECT player_id, gameweek, minutes, starts, goals_scored, assists, clean_sheets,
           goals_conceded, saves, bonus, yellow_cards, red_cards, total_points,
           expected_goals, expected_assists, expected_goal_involvements
    FROM player_gameweeks"""


def recent_gameweeks(store: Store, player_id: int, n: int) -> list[PlayerGameweekRow]:
    """A player's last `n` gameweeks, most recent first, each with its matches.

    Gameweeks he didn't play in are included (minutes = 0); gameweeks his club
    didn't play in aren't.
    """
    return _query(
        store,
        player_id,
        _SELECT + " WHERE player_id = ? ORDER BY gameweek DESC LIMIT ?",
        (player_id, n),
    )


def player_gameweek(store: Store, player_id: int, gameweek: int) -> PlayerGameweekRow | None:
    """A player's stats for one gameweek, or None if there are none: not played
    yet, a blank gameweek, or before he joined."""
    rows = _query(
        store, player_id, _SELECT + " WHERE player_id = ? AND gameweek = ?", (player_id, gameweek)
    )
    return rows[0] if rows else None


def _query(store: Store, player_id: int, sql: str, params: tuple) -> list[PlayerGameweekRow]:
    """Run a _SELECT query for one player and attach each row's matches."""
    weeks = [PlayerGameweekRow(**dict(r)) for r in store.conn.execute(sql, params)]
    _attach_matches(store, player_id, weeks)
    return weeks


# One player's matches in the given gameweeks, from his side. The "sides" step
# (a CTE: a named sub-query used by the main query) works out once whether he
# was the home team; the main query then uses that to pick his opponent and
# order the score his way round. IIF(x, a, b) is SQLite's "if x then a else b".
_MATCH_SELECT = """
    WITH sides AS (
        SELECT m.gameweek, m.fixture_id, m.minutes, m.total_points,
               f.home_team_id = m.team_id AS was_home,
               f.home_team_id, f.away_team_id, f.home_score, f.away_score,
               COALESCE(f.kickoff_time, '') AS kickoff_time
        FROM player_gameweek_matches m
        JOIN fixtures f ON f.id = m.fixture_id
        WHERE m.player_id = ? AND m.gameweek IN ({gameweeks})
    )
    SELECT gameweek, fixture_id, minutes, total_points, was_home,
           IIF(was_home, away_team_id, home_team_id) AS opponent_team_id,
           t.name AS opponent_team_name, kickoff_time,
           IIF(was_home, home_score, away_score) AS team_score,
           IIF(was_home, away_score, home_score) AS opponent_score
    FROM sides
    JOIN teams t ON t.id = IIF(was_home, away_team_id, home_team_id)
    ORDER BY kickoff_time, fixture_id"""


def _attach_matches(store: Store, player_id: int, weeks: list[PlayerGameweekRow]) -> None:
    """Fill in `matches` for each of a player's gameweek rows, with one query for all of them."""
    if not weeks:
        return
    sql = _MATCH_SELECT.format(gameweeks=placeholders(len(weeks)))
    rows = store.conn.execute(sql, (player_id, *[w.gameweek for w in weeks]))

    by_gameweek: dict[int, list[MatchRow]] = {}
    for r in rows:
        match = MatchRow(
            fixture_id=r["fixture_id"],
            opponent_team_id=r["opponent_team_id"],
            opponent_team_name=r["opponent_team_name"],
            was_home=bool(r["was_home"]),
            kickoff_time=r["kickoff_time"],
            team_score=r["team_score"],
            opponent_score=r["opponent_score"],
            minutes=r["minutes"],
            total_points=r["total_points"],
        )
        by_gameweek.setdefault(r["gameweek"], []).append(match)
    for week in weeks:
        week.matches = by_gameweek.get(week.gameweek, [])


def points_per_90_by_position(store: Store) -> dict[int, float]:
    """The average FPL points per 90 minutes played, for each position (1 GK .. 4 FWD),
    over every gameweek stored. It is what a typical player in that position
    scores, the starting point for judging someone with little history."""
    rows = store.conn.execute(
        """
        SELECT p.position, 90.0 * SUM(g.total_points) / SUM(g.minutes) AS per_90
        FROM player_gameweeks g
        JOIN players p ON p.id = g.player_id
        WHERE g.minutes > 0
        GROUP BY p.position"""
    )
    return {row["position"]: row["per_90"] for row in rows}

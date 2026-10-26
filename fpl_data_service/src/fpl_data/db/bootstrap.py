"""Saving what /bootstrap-static/ returns: teams, gameweeks, chips and players."""

from datetime import UTC, datetime, timedelta

from fpl_data import names
from fpl_data.db import sync_log
from fpl_data.db.store import Store
from fpl_data.fpl.models import Bootstrap

# Transfer snapshots older than this are deleted: they only feed a "last 24
# hours" figure, and a few days of history is plenty.
KEEP_SNAPSHOTS_FOR = timedelta(days=8)


def upsert_bootstrap(store: Store, bootstrap: Bootstrap, now: datetime | None = None) -> None:
    """Save the teams, gameweeks, chip allowances and players, record a
    transfer snapshot, and record the sync.

    All-or-nothing: if any row fails, nothing is saved. `now` is the time to
    stamp the snapshot with (tests pass their own; normally it's the real time).

    "Upsert" = insert the row, or update it if a row with that id exists, so
    running this again simply refreshes the data.
    """
    now = now or datetime.now(UTC)
    # Who is most bought and most sold this gameweek. Done here, once, rather
    # than on every query: a rank needs all the players, and queries often
    # return only a few.
    net = {p.id: p.transfers_in_event - p.transfers_out_event for p in bootstrap.elements}
    bought_rank = _ranks(net, most_first=True)
    sold_rank = _ranks(net, most_first=False)

    # One transaction for everything: all-or-nothing, and much faster than
    # committing each of the ~700 rows separately.
    with store.transaction() as conn:
        # Teams first: players.team_id REFERENCES teams(id), so each player's
        # team must already exist.
        conn.executemany(
            """
            INSERT INTO teams (id, name, short_name)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                short_name = excluded.short_name""",
            [(t.id, t.name, t.short_name) for t in bootstrap.teams],
        )

        # Gameweeks ("events" in FPL). SQLite has no boolean type: True/False are stored as 1/0.
        conn.executemany(
            """
            INSERT INTO events (id, name, deadline_time, finished, is_current, is_next)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                deadline_time = excluded.deadline_time,
                finished = excluded.finished,
                is_current = excluded.is_current,
                is_next = excluded.is_next""",
            [
                (e.id, e.name, e.deadline_time, e.finished, e.is_current, e.is_next)
                for e in bootstrap.events
            ],
        )

        # Chip allowances. Plain DELETE + INSERT: there are only 8 rows, and FPL
        # could in principle drop one, which an upsert would leave behind.
        conn.execute("DELETE FROM chips")
        conn.executemany(
            "INSERT INTO chips (id, name, start_event, stop_event) VALUES (?, ?, ?, ?)",
            [(c.id, c.name, c.start_event, c.stop_event) for c in bootstrap.chips],
        )

        # Players. The column list, the ?s and the tuple below must stay in the
        # same order. updated_at is set in the UPDATE branch because a column
        # DEFAULT only applies on INSERT.
        conn.executemany(
            """
            INSERT INTO players (
                id, web_name, first_name, second_name, search_name, team_id, position,
                now_cost, total_points, form, selected_by_percent, status, news,
                chance_of_playing_next_round,
                minutes, starts, goals_scored, assists, clean_sheets, bonus,
                expected_goals, expected_assists, expected_goal_involvements,
                points_per_game, ep_next, news_added,
                transfers_in_event, transfers_out_event, cost_change_event,
                transfers_in_total, transfers_out_total, net_transfers_percent,
                net_bought_rank, net_sold_rank, price_change_progress,
                price_projection_0, price_projection_1, price_projection_2
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                web_name = excluded.web_name,
                first_name = excluded.first_name,
                second_name = excluded.second_name,
                search_name = excluded.search_name,
                team_id = excluded.team_id,
                position = excluded.position,
                now_cost = excluded.now_cost,
                total_points = excluded.total_points,
                form = excluded.form,
                selected_by_percent = excluded.selected_by_percent,
                status = excluded.status,
                news = excluded.news,
                chance_of_playing_next_round = excluded.chance_of_playing_next_round,
                minutes = excluded.minutes,
                starts = excluded.starts,
                goals_scored = excluded.goals_scored,
                assists = excluded.assists,
                clean_sheets = excluded.clean_sheets,
                bonus = excluded.bonus,
                expected_goals = excluded.expected_goals,
                expected_assists = excluded.expected_assists,
                expected_goal_involvements = excluded.expected_goal_involvements,
                points_per_game = excluded.points_per_game,
                ep_next = excluded.ep_next,
                news_added = excluded.news_added,
                transfers_in_event = excluded.transfers_in_event,
                transfers_out_event = excluded.transfers_out_event,
                cost_change_event = excluded.cost_change_event,
                transfers_in_total = excluded.transfers_in_total,
                transfers_out_total = excluded.transfers_out_total,
                net_transfers_percent = excluded.net_transfers_percent,
                net_bought_rank = excluded.net_bought_rank,
                net_sold_rank = excluded.net_sold_rank,
                price_change_progress = excluded.price_change_progress,
                price_projection_0 = excluded.price_projection_0,
                price_projection_1 = excluded.price_projection_1,
                price_projection_2 = excluded.price_projection_2,
                updated_at = datetime('now')""",
            [
                (
                    p.id,
                    p.web_name,
                    p.first_name,
                    p.second_name,
                    # All three names, so "bukayo", "saka" and "magalhaes" all match.
                    names.normalize(f"{p.first_name} {p.second_name} {p.web_name}"),
                    p.team,
                    p.element_type,
                    p.now_cost,
                    p.total_points,
                    p.form,
                    p.selected_by_percent,
                    p.status,
                    p.news,
                    # None is stored as NULL ("no concerns"), which is not the same as 0 ("out").
                    p.chance_of_playing_next_round,
                    p.minutes,
                    p.starts,
                    p.goals_scored,
                    p.assists,
                    p.clean_sheets,
                    p.bonus,
                    p.expected_goals,
                    p.expected_assists,
                    p.expected_goal_involvements,
                    p.points_per_game,
                    p.ep_next,
                    p.news_added,
                    p.transfers_in_event,
                    p.transfers_out_event,
                    p.cost_change_event,
                    p.transfers_in,
                    p.transfers_out,
                    # This gameweek's net transfers as a share of all managers.
                    100 * net[p.id] / bootstrap.total_players if bootstrap.total_players else 0.0,
                    bought_rank[p.id],
                    sold_rank[p.id],
                    p.price_change_percent,
                    *_projection_columns(p.price_change_projections),
                )
                for p in bootstrap.elements
            ],
        )

        # A snapshot of the season totals, for "how many net transfers in the last 24 hours".
        taken_at = _format(now)
        conn.executemany(
            """
            INSERT OR REPLACE INTO transfer_snapshots (player_id, taken_at, transfers_in, transfers_out)
            VALUES (?, ?, ?, ?)""",
            [(p.id, taken_at, p.transfers_in, p.transfers_out) for p in bootstrap.elements],
        )
        conn.execute(
            "DELETE FROM transfer_snapshots WHERE taken_at < ?",
            (_format(now - KEEP_SNAPSHOTS_FOR),),
        )

        sync_log.record_sync(conn, sync_log.BOOTSTRAP)


def _ranks(values: dict[int, int], most_first: bool) -> dict[int, int]:
    """Rank each id by its value: 1 for the highest (or, if not most_first, the
    lowest). Ties are broken by id, so the ranking is always the same."""
    order = sorted(values, key=lambda i: (-values[i] if most_first else values[i], i))
    return {player_id: rank for rank, player_id in enumerate(order, start=1)}


def _projection_columns(projections) -> list[float | None]:
    """FPL's three price-change projections (by offset 0, 1, 2) as three values,
    None where FPL gave none."""
    by_offset = {pr.offset: pr.projected_percent for pr in projections}
    return [by_offset.get(offset) for offset in range(3)]


def _format(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

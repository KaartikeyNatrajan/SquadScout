"""How many managers bought and sold each player over time (the snapshots
saved by upsert_bootstrap)."""

from datetime import UTC, datetime, timedelta

from fpl_data.db.store import Store

# The reference snapshot may be at most this much older than the window asked
# for. Otherwise (the service was off for a while) it would be measuring a
# different, longer period and calling it "24 hours".
TOLERANCE = timedelta(hours=6)


def net_transfers_over(
    store: Store, window: timedelta = timedelta(hours=24), now: datetime | None = None
) -> dict[int, int]:
    """For each player, how many more managers bought him than sold him over
    the last `window`. Empty until there is a snapshot that old.

    Unlike the per-gameweek counts in `players`, which restart at every
    deadline, this compares season totals, so it works across a deadline.
    """
    now = now or datetime.now(UTC)
    wanted = _format(now - window)
    row = store.conn.execute(
        "SELECT MAX(taken_at) AS taken_at FROM transfer_snapshots WHERE taken_at <= ?", (wanted,)
    ).fetchone()
    reference = row["taken_at"]
    if reference is None:
        return {}  # no snapshot is old enough yet
    if now - datetime.fromisoformat(reference) > window + TOLERANCE:
        return {}  # the newest old-enough snapshot is too old to stand for "the last 24 hours"

    rows = store.conn.execute(
        """
        SELECT p.id, (p.transfers_in_total - s.transfers_in) - (p.transfers_out_total - s.transfers_out) AS net
        FROM players p
        JOIN transfer_snapshots s ON s.player_id = p.id AND s.taken_at = ?""",
        (reference,),
    )
    return {r["id"]: r["net"] for r in rows}


def _format(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

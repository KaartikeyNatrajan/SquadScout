"""Player lookups: by name, by id, filtered search, and transfer trends."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

from fpl_data import names
from fpl_data.db.store import Store, placeholders


@dataclass
class PlayerRow:
    """One player as stored in SQLite, joined with the team name."""

    id: int
    web_name: str
    first_name: str
    second_name: str
    team_id: int
    team_name: str
    position: int  # 1 GK .. 4 FWD
    now_cost: int  # tenths of £m
    total_points: int
    form: float
    selected_by_percent: float
    status: str  # a, d, i, s, u, n
    news: str
    news_added: str  # RFC 3339; "" if no news
    chance_of_playing_next_round: int | None  # None = no concerns
    minutes: int
    starts: int
    goals_scored: int
    assists: int
    clean_sheets: int
    bonus: int
    expected_goals: float
    expected_assists: float
    expected_goal_involvements: float
    points_per_game: float
    expected_points_next: float  # FPL's ep_next
    transfers_in_event: int  # by all managers since the last deadline
    transfers_out_event: int
    cost_change_event: int  # tenths of £m since the last deadline
    net_transfers_percent: float  # this gameweek's net transfers, as % of all managers
    net_bought_rank: int  # 1 = the player most net bought by managers this gameweek
    net_sold_rank: int  # 1 = the player most net sold
    price_change_progress: float  # -100 (a fall) .. +100 (a rise); see fpl/models.py
    price_projections: list[
        float
    ]  # that progress after the next 1, 2, 3 price updates (may be shorter)


# The SELECT shared by every player query; each query adds its own WHERE /
# ORDER BY. The column names (after AS) match PlayerRow's fields, so a result
# row converts with PlayerRow(**row).
_SELECT = """
    SELECT p.id, p.web_name, p.first_name, p.second_name, p.team_id, t.name AS team_name,
           p.position, p.now_cost, p.total_points, p.form, p.selected_by_percent,
           p.status, p.news, COALESCE(p.news_added, '') AS news_added,
           p.chance_of_playing_next_round,
           p.minutes, p.starts, p.goals_scored, p.assists, p.clean_sheets, p.bonus,
           p.expected_goals, p.expected_assists, p.expected_goal_involvements,
           p.points_per_game, p.ep_next AS expected_points_next,
           p.transfers_in_event, p.transfers_out_event, p.cost_change_event,
           p.net_transfers_percent, p.net_bought_rank, p.net_sold_rank, p.price_change_progress,
           p.price_projection_0, p.price_projection_1, p.price_projection_2
    FROM players p
    JOIN teams t ON t.id = p.team_id"""


def _query(store: Store, sql: str, params: Sequence = ()) -> list[PlayerRow]:
    rows = store.conn.execute(sql, params).fetchall()
    return [_row(row) for row in rows]


def _row(row) -> PlayerRow:
    """A result row as a PlayerRow. The three price projection columns become one list."""
    values = dict(row)
    projections = [values.pop(f"price_projection_{i}") for i in range(3)]
    return PlayerRow(**values, price_projections=[x for x in projections if x is not None])


def search(store: Store, name: str, limit: int) -> list[PlayerRow]:
    """Players whose name contains `name`, ignoring case and accents, best total
    points first, at most `limit` rows. No match is an empty list, not an error."""
    # '%' || ? || '%' wraps the parameter in wildcards: "name contains ?".
    # Sorting by points puts the likeliest match first when several players
    # share a name (Wilson, Gomez, ...).
    sql = (
        _SELECT
        + """
        WHERE p.search_name LIKE '%' || ? || '%'
        ORDER BY p.total_points DESC
        LIMIT ?"""
    )
    # Normalise the search text the same way search_name was built, so
    # "Ødegaard" and "odegaard" both match.
    return _query(store, sql, (names.normalize(name), limit))


def get_player(store: Store, player_id: int) -> PlayerRow | None:
    """One player by id, or None if there's no such player."""
    rows = _query(store, _SELECT + " WHERE p.id = ?", (player_id,))
    return rows[0] if rows else None


def players_by_id(store: Store, ids: Sequence[int]) -> dict[int, PlayerRow]:
    """The players with these ids, keyed by id. Ids with no player are simply
    missing from the result; callers check."""
    if not ids:
        return {}
    rows = _query(store, _SELECT + f" WHERE p.id IN ({placeholders(len(ids))})", tuple(ids))
    return {p.id: p for p in rows}


class PlayerSort(Enum):
    """A column find_players can sort by, best first. The value is the SQL.

    ORDER BY can't take a ? parameter, so the column has to go into the SQL
    text. Using a fixed set of values (instead of text from the request) makes
    that safe: only these four strings can ever reach the database.
    """

    EXPECTED_POINTS = "p.ep_next"
    FORM = "p.form"
    TOTAL_POINTS = "p.total_points"
    POINTS_PER_GAME = "p.points_per_game"


@dataclass
class PlayerFilter:
    """Which players find_players returns. Defaults mean "no filter", so
    PlayerFilter(limit=5) is the 5 best players overall."""

    position: int = 0  # 1 GK .. 4 FWD; 0 = any
    max_cost: int = 0  # tenths of £m; 0 = no limit
    include_unavailable: bool = False  # include injured (i), suspended (s), unavailable (u, n)
    exclude_ids: list[int] = field(
        default_factory=list
    )  # e.g. players already in a manager's squad
    sort: PlayerSort = PlayerSort.EXPECTED_POINTS
    limit: int = 5


def find_players(store: Store, flt: PlayerFilter) -> list[PlayerRow]:
    """Players matching `flt`, best first by flt.sort (ties broken by total points)."""
    # Build the WHERE clause from whichever filters are set. Each condition has
    # ?s, and its values are appended to `params` in the same order.
    conditions: list[str] = []
    params: list = []
    if flt.position:
        conditions.append("p.position = ?")
        params.append(flt.position)
    if flt.max_cost > 0:
        conditions.append("p.now_cost <= ?")
        params.append(flt.max_cost)
    if not flt.include_unavailable:
        # Available and doubtful players only.
        conditions.append("p.status IN ('a', 'd')")
    if flt.exclude_ids:
        conditions.append(f"p.id NOT IN ({placeholders(len(flt.exclude_ids))})")
        params.extend(flt.exclude_ids)

    sql = _SELECT
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += f" ORDER BY {flt.sort.value} DESC, p.total_points DESC LIMIT ?"
    params.append(flt.limit)
    return _query(store, sql, params)


def top_transfers(store: Store, transferred_in: bool, limit: int) -> list[PlayerRow]:
    """The players most transferred in (or out) since the last deadline, most first."""
    # Same reasoning as PlayerSort: the column comes from our own code.
    column = "p.transfers_in_event" if transferred_in else "p.transfers_out_event"
    return _query(store, _SELECT + f" ORDER BY {column} DESC LIMIT ?", (limit,))

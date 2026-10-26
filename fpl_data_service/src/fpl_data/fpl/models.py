"""Models mirroring the JSON the FPL API sends.

Pydantic decodes the JSON into these classes. Keys we don't list are ignored,
so a model only has to describe the fields we use. Two FPL quirks are handled
here, so the rest of the code never sees them:

- Many decimals arrive as strings ("6.2"). Fields typed `float` convert them.
- Some fields can be JSON null. Those are typed `X | None`: None means null.
  (A plain int would hide the difference between "0% chance of playing" =
  definitely out, and null = no concerns.)
"""

from pydantic import BaseModel

# ---- /bootstrap-static/ -------------------------------------------------


class PriceProjection(BaseModel):
    """FPL's projection of a player's progress towards a price change, `offset`
    price updates from now (offset 0 = the next one)."""

    offset: int
    projected_percent: float
    likelihood: int


class Element(BaseModel):
    """FPL's name for a player."""

    id: int
    web_name: str
    first_name: str
    second_name: str
    team: int
    element_type: int  # 1 GK, 2 DEF, 3 MID, 4 FWD
    now_cost: int  # tenths of £m
    total_points: int
    form: float
    selected_by_percent: float
    status: str  # a, d, i, s, u, n
    news: str  # "" when there is none
    chance_of_playing_next_round: int | None
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
    ep_next: float  # FPL's expected points for the next gameweek
    transfers_in_event: int  # by all managers since the last deadline
    transfers_out_event: int
    cost_change_event: int  # tenths of £m since the last deadline
    news_added: str | None  # RFC 3339; null if no news
    transfers_in: int  # by all managers, the whole season
    transfers_out: int
    # How far the player is towards his next price change, from -100 (a fall) to
    # +100 (a rise). FPL doesn't document this field. It follows net transfers
    # (positive when more managers buy than sell) and goes back to about 0 after a
    # price change, which is the reading the rest of the code relies on.
    price_change_percent: float
    price_change_projections: list[PriceProjection]


class Team(BaseModel):
    """One Premier League club."""

    id: int
    name: str
    short_name: str


class Event(BaseModel):
    """FPL's name for a gameweek."""

    id: int
    name: str
    deadline_time: str
    is_current: bool
    is_next: bool
    finished: bool


class Chip(BaseModel):
    """A chip allowance: each manager may play it once between start_event and stop_event.

    FPL gives two of each chip per season, one per half, so each name appears twice.
    """

    id: int
    name: str  # "wildcard", "freehit", "bboost", "3xc"
    start_event: int
    stop_event: int


class Bootstrap(BaseModel):
    """/bootstrap-static/: every player, team and gameweek in one ~3 MB response."""

    elements: list[Element]
    teams: list[Team]
    events: list[Event]
    chips: list[Chip]
    total_players: int  # how many managers are playing FPL


# ---- /fixtures/ -----------------------------------------------------------


class Fixture(BaseModel):
    """One match. "h" is the home team, "a" the away team."""

    id: int
    event: int | None  # gameweek; null if not scheduled yet
    kickoff_time: str | None  # RFC 3339; null if not scheduled yet
    team_h: int
    team_a: int
    team_h_difficulty: int  # 1 (easy) - 5 (hard)
    team_a_difficulty: int
    finished: bool
    team_h_score: int | None  # null until the match starts
    team_a_score: int | None


# ---- /event/{gw}/live/ ----------------------------------------------------
# Every player's stats for one gameweek in one response. It's how we build
# players' match histories: one download per gameweek covers all ~700 players.


class LiveStats(BaseModel):
    """A player's totals for one gameweek (all of his matches in it)."""

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


class LiveExplainStat(BaseModel):
    """One scoring line, e.g. {identifier: "minutes", value: 80, points: 2}."""

    identifier: str  # "minutes", "goals_scored", "bonus", ...
    value: int
    points: int
    points_modification: int = 0


class LiveExplain(BaseModel):
    """How a player scored his points in one match."""

    fixture: int
    stats: list[LiveExplainStat]

    def minutes(self) -> int:
        """The minutes he played in this match."""
        for stat in self.stats:
            if stat.identifier == "minutes":
                return stat.value
        return 0

    def points(self) -> int:
        """The FPL points he scored in this match: the sum of every scoring line.

        Across all of a gameweek's matches this adds up to LiveStats.total_points.
        """
        return sum(stat.points + stat.points_modification for stat in self.stats)


class LiveElement(BaseModel):
    """One player's gameweek."""

    id: int  # player id
    stats: LiveStats  # totals for the whole gameweek
    # One entry per match his team played that gameweek: usually one, two in a
    # double gameweek, none in a blank one. Present even if he didn't play.
    explain: list[LiveExplain]


class EventLive(BaseModel):
    elements: list[LiveElement]


# ---- /element-summary/{id}/ ----------------------------------------------
# One player's history. Of it we only use his price in each gameweek, to find
# out what a manager paid for a player who was in the squad from the start.


class ElementHistoryEntry(BaseModel):
    round: int  # the gameweek
    value: int  # his price that gameweek, tenths of £m


class ElementSummary(BaseModel):
    history: list[ElementHistoryEntry]


# ---- /entry/{id}/... (a manager's team; FPL calls a team an "entry") ------
# The id is the number in the URL of the manager's points page. Picks are only
# published once a gameweek's deadline has passed.


class Entry(BaseModel):
    """/entry/{id}/"""

    id: int
    name: str  # the team's name
    player_first_name: str
    player_last_name: str
    started_event: int  # first gameweek the team played
    # The last gameweek whose deadline has passed; null before the team's first deadline.
    current_event: int | None
    summary_overall_points: int
    summary_overall_rank: int | None  # null before the first gameweek is scored


class EntryGameweek(BaseModel):
    """One team's numbers for one gameweek."""

    event: int
    points: int = 0
    total_points: int = 0
    bank: int = 0  # tenths of £m, after that gameweek's transfers
    value: int = 0  # squad value, tenths of £m
    # Transfers made for this gameweek and the points they cost. Transfers made
    # with a Wildcard or Free Hit aren't counted here.
    event_transfers: int = 0
    event_transfers_cost: int = 0


class ChipPlay(BaseModel):
    """A chip a team has played."""

    name: str
    event: int


class EntryHistory(BaseModel):
    """/entry/{id}/history/"""

    current: list[EntryGameweek]  # one per gameweek played this season
    chips: list[ChipPlay]


class Pick(BaseModel):
    """One of the 15 players in a squad."""

    element: int  # player id
    position: int  # 1-11 starting XI, 12-15 bench
    multiplier: int
    is_captain: bool
    is_vice_captain: bool


class EntryPicks(BaseModel):
    """/entry/{id}/event/{gw}/picks/"""

    active_chip: str | None  # null if no chip was played
    entry_history: EntryGameweek
    picks: list[Pick]


class EntryTransfer(BaseModel):
    """One transfer from /entry/{id}/transfers/."""

    element_in: int
    element_in_cost: int  # what he was bought for, tenths of £m
    element_out: int
    element_out_cost: int
    event: int  # the gameweek the transfer was made for

"""Conversions from database rows (package db) to protobuf messages (package pb).

Keeping them here means the db package never depends on gRPC, and each RPC
handler stays short. Money is stored in tenths of £m and converted here.
"""

import math

from fpl_data import sentiment
from fpl_data.db.fixtures import FixtureRow
from fpl_data.db.player_gameweeks import PlayerGameweekRow
from fpl_data.db.players import PlayerRow
from squadscout.v1 import fpl_pb2 as pb

# FPL's one-letter status codes, spelled out so the model reads "injured"
# instead of having to know what "i" means.
_AVAILABILITY_BY_STATUS = {
    "a": "available",
    "d": "doubtful",
    "i": "injured",
    "s": "suspended",
    "u": "unavailable",  # e.g. left the club, or on loan outside the league
    "n": "ineligible",  # e.g. on loan at a club that can't field him against his parent club
}


def availability(status: str) -> str:
    return _AVAILABILITY_BY_STATUS.get(status, "unknown")


def pounds(tenths: int) -> float:
    """Convert tenths of £m (how FPL stores money) to £m: 95 -> 9.5."""
    return tenths / 10


def round2(x: float) -> float:
    """Round to 2 decimal places, halves away from zero (the way people round).

    Adding up floats like 0.23 + 1.38 + 0.34 gives 1.9499999999999997, not
    1.95, so sums are rounded before sending. (Python's own round() rounds
    halves to even, e.g. round(0.125, 2) == 0.12, which isn't what we want.)
    """
    scaled = abs(x) * 100
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    return math.copysign(whole, x) / 100


def to_player(p: PlayerRow, net_transfers_24h: int | None = None) -> pb.Player:
    """`net_transfers_24h` is his net transfers over the last 24 hours, if known."""
    return pb.Player(
        id=p.id,
        web_name=p.web_name,
        first_name=p.first_name,
        second_name=p.second_name,
        team_id=p.team_id,
        team_name=p.team_name,
        position=p.position,  # the db's 1-4 match the proto enum's numbers
        price=pounds(p.now_cost),
        total_points=p.total_points,
        form=p.form,
        selected_by_percent=p.selected_by_percent,
        status=p.status,
        availability=availability(p.status),
        news=p.news,
        chance_of_playing_next_round=p.chance_of_playing_next_round,  # None stays unset
        minutes=p.minutes,
        starts=p.starts,
        goals_scored=p.goals_scored,
        assists=p.assists,
        clean_sheets=p.clean_sheets,
        bonus=p.bonus,
        expected_goals=p.expected_goals,
        expected_assists=p.expected_assists,
        expected_goal_involvements=p.expected_goal_involvements,
        points_per_game=p.points_per_game,
        expected_points_next=p.expected_points_next,
        transfers_in_event=p.transfers_in_event,
        transfers_out_event=p.transfers_out_event,
        price_change_event=pounds(p.cost_change_event),
        news_added=p.news_added,
        net_transfers_event=p.transfers_in_event - p.transfers_out_event,
        net_transfers_percent=round(p.net_transfers_percent, 3),
        net_bought_rank=p.net_bought_rank,
        net_sold_rank=p.net_sold_rank,
        net_transfers_24h=net_transfers_24h,  # None stays unset
        transfer_sentiment=sentiment.transfer_sentiment(
            p.transfers_in_event - p.transfers_out_event,
            p.net_transfers_percent,
            p.net_bought_rank,
            p.net_sold_rank,
        ),
        price_change_progress=round(p.price_change_progress, 1),
        price_change_outlook=sentiment.price_outlook(p.price_change_progress, p.price_projections),
    )


def to_fixture(f: FixtureRow) -> pb.Fixture:
    return pb.Fixture(
        id=f.id,
        gameweek=f.gameweek,
        home_team_id=f.home_team_id,
        home_team_name=f.home_team_name,
        away_team_id=f.away_team_id,
        away_team_name=f.away_team_name,
        kickoff_time=f.kickoff_time,
        home_difficulty=f.home_difficulty,
        away_difficulty=f.away_difficulty,
        finished=f.finished,
        home_score=f.home_score,
        away_score=f.away_score,
    )


def to_player_fixture(f: FixtureRow, team_id: int) -> pb.PlayerFixture:
    """A fixture seen from `team_id`'s side: who they play, whether at home,
    and how hard it is for them."""
    is_home = f.home_team_id == team_id
    return pb.PlayerFixture(
        fixture_id=f.id,
        gameweek=f.gameweek,
        kickoff_time=f.kickoff_time,
        opponent_team_id=f.away_team_id if is_home else f.home_team_id,
        opponent_team_name=f.away_team_name if is_home else f.home_team_name,
        is_home=is_home,
        difficulty=f.home_difficulty if is_home else f.away_difficulty,
    )


def to_gameweek_stats(g: PlayerGameweekRow) -> pb.GameweekStats:
    """A player's gameweek. Each match goes into match_results; when there was
    exactly one (almost always), its details are also copied to the top-level
    fixture fields for convenience."""
    stats = pb.GameweekStats(
        player_id=g.player_id,
        gameweek=g.gameweek,
        minutes=g.minutes,
        starts=g.starts,
        goals_scored=g.goals_scored,
        assists=g.assists,
        clean_sheets=g.clean_sheets,
        goals_conceded=g.goals_conceded,
        saves=g.saves,
        bonus=g.bonus,
        yellow_cards=g.yellow_cards,
        red_cards=g.red_cards,
        total_points=g.total_points,
        expected_goals=g.expected_goals,
        expected_assists=g.expected_assists,
        expected_goal_involvements=g.expected_goal_involvements,
        match_results=[
            pb.MatchResult(
                fixture_id=m.fixture_id,
                opponent_team_id=m.opponent_team_id,
                opponent_team_name=m.opponent_team_name,
                was_home=m.was_home,
                kickoff_time=m.kickoff_time,
                team_score=m.team_score,
                opponent_score=m.opponent_score,
                minutes=m.minutes,
                total_points=m.total_points,
            )
            for m in g.matches
        ],
    )
    if len(g.matches) == 1:
        m = g.matches[0]
        stats.fixture_id = m.fixture_id
        stats.opponent_team_id = m.opponent_team_id
        stats.opponent_team_name = m.opponent_team_name
        stats.was_home = m.was_home
        stats.kickoff_time = m.kickoff_time
        if m.team_score is not None:
            stats.team_score = m.team_score
        if m.opponent_score is not None:
            stats.opponent_score = m.opponent_score
    return stats

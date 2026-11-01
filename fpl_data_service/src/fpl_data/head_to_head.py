"""Head-to-head records between two clubs, from their past results.

Pure (no I/O): given the meetings, it works out the win/draw/loss record from
one club's side, a one-line summary for fixture lists, and which players
scored or assisted in them.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from fpl_data.history.results import Result


@dataclass
class Record:
    played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0


def scores_for(team_code: int, r: Result) -> tuple[int, int]:
    """(the club's goals, the opponent's goals) in one match."""
    return (
        (r.home_score, r.away_score) if r.home.code == team_code else (r.away_score, r.home_score)
    )


def outcome(team_code: int, r: Result) -> str:
    """ "W", "D" or "L" for the club."""
    ours, theirs = scores_for(team_code, r)
    return "W" if ours > theirs else "L" if ours < theirs else "D"


def record(team_code: int, meetings: Sequence[Result]) -> Record:
    rec = Record()
    for r in meetings:
        ours, theirs = scores_for(team_code, r)
        rec.played += 1
        rec.goals_for += ours
        rec.goals_against += theirs
        match outcome(team_code, r):
            case "W":
                rec.wins += 1
            case "D":
                rec.draws += 1
            case _:
                rec.losses += 1
    return rec


def summary(team_code: int, meetings: Sequence[Result], since: str) -> str:
    """The record in one line, from the club's side, e.g. "last 6 PL meetings since
    2021-22: W3 D1 L2, goals 9-7; last: W 2-1 at home (2026-02-12)".

    `meetings` must be newest first; `since` is the oldest season searched.
    """
    if not meetings:
        return f"no Premier League meetings since {since}"
    rec = record(team_code, meetings)
    last = meetings[0]
    ours, theirs = scores_for(team_code, last)
    venue = "at home" if last.home.code == team_code else "away"
    plural = "meeting" if rec.played == 1 else "meetings"
    return (
        f"last {rec.played} PL {plural} since {since}: W{rec.wins} D{rec.draws} L{rec.losses}, "
        f"goals {rec.goals_for}-{rec.goals_against}; "
        f"last: {outcome(team_code, last)} {ours}-{theirs} {venue} ({last.kickoff_time[:10]})"
    )


def contributions(meetings: Sequence[Result]) -> dict[int, tuple[int, int]]:
    """Goals and assists in these meetings per player (by lasting code), whichever
    club he played for."""
    goals: Counter[int] = Counter()
    assists: Counter[int] = Counter()
    for r in meetings:
        for e in r.events:
            if e.kind == "goal":
                goals[e.player_code] += e.count
            elif e.kind == "assist":
                assists[e.player_code] += e.count
    return {code: (goals[code], assists[code]) for code in goals.keys() | assists.keys()}

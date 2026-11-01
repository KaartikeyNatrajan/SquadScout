"""Turning FPL fixtures (and their per-match stats) into results.

The same code reads the current season (from the FPL API) and past seasons
(from the archive), because both are in FPL's format. It is pure: no I/O.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from fpl_data.fpl.models import Fixture
from fpl_data.history.seasons import season_of

# FPL's stat identifiers -> our event kinds. Others (bonus, saves, yellow
# cards, ...) aren't kept: they add little to "how did this fixture go?".
EVENT_KINDS = {
    "goals_scored": "goal",
    "assists": "assist",
    "own_goals": "own_goal",
    "penalties_saved": "penalty_saved",
    "penalties_missed": "penalty_missed",
    "red_cards": "red_card",
}


@dataclass(frozen=True)
class Club:
    code: int  # the club's id in every season
    name: str  # what FPL called it that season


@dataclass(frozen=True)
class Person:
    code: int  # the player's id in every season
    name: str  # his FPL web name that season


@dataclass
class MatchEvent:
    kind: str  # a value of EVENT_KINDS
    team_code: int  # the club he played for in the match
    player_code: int
    player_name: str
    count: int


@dataclass
class Result:
    """A finished match."""

    season: str
    fixture_id: int  # FPL's id, unique within a season
    kickoff_time: str
    home: Club
    away: Club
    home_score: int
    away_score: int
    events: list[MatchEvent] = field(default_factory=list)


def to_results(
    fixtures: Sequence[Fixture], clubs: Mapping[int, Club], people: Mapping[int, Person]
) -> list[Result]:
    """The finished fixtures as results. `clubs` and `people` map that season's team
    and player ids to the clubs and players. Players missing from `people` are left
    out of the events; matches with an unknown club are skipped."""
    results = []
    for f in fixtures:
        if not f.finished or f.team_h_score is None or f.team_a_score is None:
            continue
        home, away = clubs.get(f.team_h), clubs.get(f.team_a)
        if home is None or away is None or not f.kickoff_time:
            continue
        results.append(
            Result(
                season=season_of(datetime.fromisoformat(f.kickoff_time)),
                fixture_id=f.id,
                kickoff_time=f.kickoff_time,
                home=home,
                away=away,
                home_score=f.team_h_score,
                away_score=f.team_a_score,
                events=_events(f, home, away, people),
            )
        )
    return results


def _events(f: Fixture, home: Club, away: Club, people: Mapping[int, Person]) -> list[MatchEvent]:
    events = []
    for stat in f.stats:
        kind = EVENT_KINDS.get(stat.identifier)
        if kind is None:
            continue
        for club, values in ((home, stat.h), (away, stat.a)):
            for v in values:
                person = people.get(v.element)
                if person is not None and v.value > 0:
                    events.append(MatchEvent(kind, club.code, person.code, person.name, v.value))
    return events

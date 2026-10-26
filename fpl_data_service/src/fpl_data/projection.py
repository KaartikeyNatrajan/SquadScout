"""Expected points over the next few gameweeks.

FPL only publishes expected points for the *next* gameweek (`ep_next`). But
"is this transfer worth a -4 hit?" is a question about the next several weeks,
so we estimate our own. It is deliberately simple and explainable:

    expected points in a match =
        chance he is fit and available
      x expected minutes / 90                  from his last few gameweeks
      x points he scores per 90 minutes        from his last few gameweeks,
                                               pulled towards the position average
      x opponent factor                        easier fixture -> more, harder -> less

and a gameweek's projection is the sum over its matches: a double gameweek
counts twice, a blank gameweek counts zero.

The tunable numbers below were chosen by backtesting: predict each gameweek
from the ones before it, for every player, and compare with what happened.
On real gameweeks 4-6 (about 2,000 predictions) it was unbiased overall and
beat the naive guess "the average of his last gameweeks": an average error of
1.19 points per player against 1.25 (and 2.62 against 2.74 for regular
starters). That is a modest edge, because football points are very noisy, so
treat the projection as a rough guide; the responses say so. It also knows
nothing about injuries during the backtest, so on regular starters it ran a
little optimistic (+0.4 points).

Like squad.py, this module is pure: no database, no network, which keeps it
easy to test.
"""

from collections.abc import Sequence
from dataclasses import dataclass

# How many of a player's most recent gameweeks to learn from.
RECENT_GAMEWEEKS = 4
# How much to trust the position average over his own numbers, measured in
# "full matches" worth of evidence. With few minutes played his own points per
# 90 is noisy (one goal in 20 minutes looks like a superstar), so it is blended
# with what an average player in his position scores.
PRIOR_MATCHES = 3.0
# Each step of FPL's fixture difficulty rating (1 easy .. 5 hard) away from the
# middle (3) changes expected points by this fraction.
DIFFICULTY_STEP = 0.12
# A player with no history yet (new signing): assume he plays this many minutes.
NO_HISTORY_MINUTES = 45.0
# A doubtful player's chance of playing recovers over this many weeks.
RECOVERY_WEEKS = 3


@dataclass(frozen=True)
class PastGameweek:
    """What a player did in one gameweek he could have played in."""

    minutes: int
    points: int
    matches: int  # how many matches his club played that week (1, or 2 in a double gameweek)


@dataclass(frozen=True)
class UpcomingMatch:
    gameweek: int
    difficulty: int  # FPL's 1 (easy) .. 5 (hard), for the player's team


@dataclass(frozen=True)
class GameweekProjection:
    gameweek: int
    expected_points: float
    matches: int  # 0 in a blank gameweek


@dataclass(frozen=True)
class Projection:
    gameweeks: tuple[GameweekProjection, ...]

    @property
    def total(self) -> float:
        return sum(g.expected_points for g in self.gameweeks)


def availability(status: str, chance_of_playing: int | None, weeks_ahead: int) -> float:
    """The chance (0 to 1) that the player is fit to play, `weeks_ahead` weeks
    after the next gameweek (0 = the next gameweek).

    FPL's `chance_of_playing_next_round` is about the next gameweek only. We
    assume a doubtful player (chance above 0) steadily gets fitter over
    RECOVERY_WEEKS. A player ruled out (0%, no return date) stays out for the
    whole projection, which is pessimistic but honest: we don't know when he is back.
    """
    if chance_of_playing is not None:
        chance = chance_of_playing / 100
        if chance <= 0:
            return 0.0
        return chance + (1 - chance) * min(1.0, weeks_ahead / RECOVERY_WEEKS)
    if status == "a":
        return 1.0
    if status == "d":
        return 0.75  # doubtful with no percentage given
    return 0.0  # injured, suspended, unavailable, ineligible


def difficulty_factor(difficulty: int) -> float:
    """Easier opponents give more points: 1.24 for difficulty 1, 1.0 for 3, 0.76 for 5."""
    return 1 + DIFFICULTY_STEP * (3 - difficulty)


def expected_minutes_per_match(past: Sequence[PastGameweek]) -> float:
    """The minutes he averaged per match over his recent gameweeks."""
    recent = past[:RECENT_GAMEWEEKS]
    matches = sum(g.matches for g in recent)
    if matches == 0:
        return NO_HISTORY_MINUTES
    return min(sum(g.minutes for g in recent) / matches, 90.0)


def points_per_90(past: Sequence[PastGameweek], baseline_per_90: float) -> float:
    """The points he scores per 90 minutes, pulled towards `baseline_per_90`
    (the position average) the less evidence there is."""
    recent = past[:RECENT_GAMEWEEKS]
    matches_played = sum(g.minutes for g in recent) / 90
    points = sum(g.points for g in recent)
    rate = (points + baseline_per_90 * PRIOR_MATCHES) / (matches_played + PRIOR_MATCHES)
    # A projection is never negative (a bad run of cards and own goals could pull the
    # rate below zero). optimizer.py relies on this.
    return max(rate, 0.0)


def project(
    *,
    past: Sequence[PastGameweek],
    upcoming: Sequence[UpcomingMatch],
    first_gameweek: int,
    horizon: int,
    baseline_per_90: float,
    status: str = "a",
    chance_of_playing: int | None = None,
) -> Projection:
    """Project a player's points for `horizon` gameweeks starting at `first_gameweek`.

    `past` is his recent gameweeks, most recent first. `upcoming` is his
    club's fixtures (the ones outside the horizon are ignored).
    """
    minutes = expected_minutes_per_match(past)
    rate = points_per_90(past, baseline_per_90)

    gameweeks = []
    for weeks_ahead in range(horizon):
        gameweek = first_gameweek + weeks_ahead
        matches = [m for m in upcoming if m.gameweek == gameweek]
        fit = availability(status, chance_of_playing, weeks_ahead)
        expected = sum(
            fit * (minutes / 90) * rate * difficulty_factor(m.difficulty) for m in matches
        )
        gameweeks.append(GameweekProjection(gameweek, expected, len(matches)))
    return Projection(tuple(gameweeks))

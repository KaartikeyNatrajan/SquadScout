"""Turning data-service replies into short text for the model.

Tool results are sent to the model as input on every later model call of the
conversation, so their size is paid for again and again (and a local model has
only a few thousand tokens of room). The raw JSON of one player is about 3,700
characters, mostly field names and numbers the model doesn't need. These
functions keep what answers FPL questions, in a compact "label: value" form,
and always keep the ids that other tools need.
"""

from squadscout.v1 import fpl_pb2 as pb

POSITION_NAMES = {
    pb.POSITION_GOALKEEPER: "goalkeeper",
    pb.POSITION_DEFENDER: "defender",
    pb.POSITION_MIDFIELDER: "midfielder",
    pb.POSITION_FORWARD: "forward",
}


def players(response: pb.SearchPlayersResponse) -> str:
    """Every player found, one short block each."""
    if not response.players:
        return "No players found with that name."
    return "\n\n".join(player(p) for p in response.players)


def player(p: pb.Player) -> str:
    """One player in five lines, e.g.:

    Saka (player id 12): Arsenal (team id 1), midfielder, £9.5m
      availability: injured. News: Thigh injury - Unknown return date. 0% chance of playing next round
      season: 37 pts (5.3 per game), form 1.2, 7 starts, 596 min, 3 goals, 0 assists, 4 clean sheets, 4 bonus
      expected: xG 3.63, xA 1.20, FPL expected points next gameweek 0.0
      managers: owned by 6.5%; being sold: net -0.28% ...; price: no change expected ...
    """
    position = POSITION_NAMES.get(p.position, "unknown position")
    header = (
        f"{p.web_name} (player id {p.id}): {p.team_name} (team id {p.team_id}), "
        f"{position}, £{p.price:.1f}m"
    )
    season = (
        f"{p.total_points} pts ({p.points_per_game} per game), form {p.form}, "
        f"{p.starts} starts, {p.minutes} min, {p.goals_scored} goals, {p.assists} assists, "
        f"{p.clean_sheets} clean sheets, {p.bonus} bonus"
    )
    expected = (
        f"xG {p.expected_goals:.2f}, xA {p.expected_assists:.2f}, "
        f"FPL expected points next gameweek {p.expected_points_next:.1f}"
    )
    managers = (
        f"owned by {p.selected_by_percent}%; {p.transfer_sentiment}; "
        f"price: {p.price_change_outlook}"
    )
    return "\n".join(
        [
            header,
            f"  availability: {_availability(p)}",
            f"  season: {season}",
            f"  expected: {expected}",
            f"  managers: {managers}",
        ]
    )


def _availability(p: pb.Player) -> str:
    """ "available", or the status with FPL's news and chance of playing."""
    parts = [p.availability or "unknown"]
    if p.news:
        parts.append(f"News: {p.news}")
    # `chance_of_playing_next_round` is optional: unset means FPL has no concerns,
    # and 100% says nothing new either; only a lower chance is worth the words.
    if p.HasField("chance_of_playing_next_round") and p.chance_of_playing_next_round < 100:
        parts.append(f"{p.chance_of_playing_next_round}% chance of playing next round")
    return ". ".join(parts)


def fixtures(response: pb.GetFixturesResponse) -> str:
    """One line per fixture, soonest first."""
    if not response.fixtures:
        return "No fixtures left."
    return "\n".join(fixture(f) for f in response.fixtures)


def fixture(f: pb.Fixture) -> str:
    """One fixture on one line, e.g.:

    GW10 2026-11-07 15:00 UTC: Arsenal (home, difficulty 2) v Hull City (away, difficulty 5).
    Past meetings, from Arsenal's side: no Premier League meetings since 2021-22
    """
    when = f.kickoff_time[:16].replace("T", " ") + " UTC" if f.kickoff_time else "date not set"
    line = (
        f"GW{f.gameweek} {when}: {f.home_team_name} (home, difficulty {f.home_difficulty}) "
        f"v {f.away_team_name} (away, difficulty {f.away_difficulty})"
    )
    # The scores are optional: unset until the match starts.
    if f.HasField("home_score"):
        state = "result" if f.finished else "in progress"
        line += f". {state}: {f.home_score}-{f.away_score}"
    if f.head_to_head:
        line += f". Past meetings, from {f.home_team_name}'s side: {f.head_to_head}"
    return line


POSITION_SHORT = {
    pb.POSITION_GOALKEEPER: "GK",
    pb.POSITION_DEFENDER: "DEF",
    pb.POSITION_MIDFIELDER: "MID",
    pb.POSITION_FORWARD: "FWD",
}


def manager_team(response: pb.GetManagerTeamResponse) -> str:
    """A manager's whole squad in about 20 lines (~3,400 characters; the JSON is ~41,000), e.g.:

    Bol Uwd Utd (team id 6), manager Osian Roberts: 637 points, overall rank 64,895.
    Squad as of the gameweek 9 deadline; next is gameweek 10 (deadline 2026-11-06 18:30 UTC).
    Bank £1.1m, squad value £101.1m, 1 free transfer(s), chips left: freehit.
    Starting XI:
      GK Raya (player id 1, Arsenal) sells £6.1m: available; form 6.0; projected 5.6 pts
        next GW, 24.8 over 5 GWs; next: Liverpool (A, 4), Hull City (H, 2), Newcastle (A, 3)
      ...
    Bench, in substitution order:
      ...
    Notes: ...
    """
    r = response
    deadline = r.next_deadline[:16].replace("T", " ") + " UTC" if r.next_deadline else "unknown"
    chips = ", ".join(r.chips_available) or "none"
    who = (
        f"{r.team_name} (team id {r.team_id}), manager {r.manager_name}: "
        f"{r.overall_points} points, overall rank {r.overall_rank:,}."
    )
    when = (
        f"Squad as of the gameweek {r.squad_gameweek} deadline; next is gameweek "
        f"{r.next_gameweek} (deadline {deadline})."
    )
    money = (
        f"Bank £{r.bank:.1f}m, squad value £{r.squad_value:.1f}m, "
        f"{r.free_transfers} free transfer(s), chips left: {chips}."
    )
    lines = [who, when, money, "Starting XI:"]
    # squad_position 1-11 are the starters, 12-15 the bench in substitution order.
    for member in r.players:
        if member.squad_position == 12:
            lines.append("Bench, in substitution order:")
        lines.append("  " + _squad_player(member))
    if r.notes:
        lines.append("Notes: " + " ".join(r.notes))
    return "\n".join(lines)


def _squad_player(m: pb.SquadPlayer) -> str:
    """One squad member on one line: who, what he'd sell for, fitness, outlook."""
    p = m.player
    role = " (captain)" if m.is_captain else " (vice-captain)" if m.is_vice_captain else ""
    estimate = " (estimate)" if m.selling_price_is_estimate else ""
    upcoming = ", ".join(
        f"{f.opponent_team_name} ({'H' if f.is_home else 'A'}, {f.difficulty})"
        for f in m.next_fixtures
    )
    return (
        f"{POSITION_SHORT.get(p.position, '?')} {p.web_name}{role} "
        f"(player id {p.id}, {p.team_name}) sells £{m.selling_price:.1f}m{estimate}: "
        f"{_availability(p)}; form {p.form}; {_projected(m.projection)}; "
        f"next: {upcoming or 'none'}"
    )


def _projected(projection: pb.Projection) -> str:
    """Expected points next gameweek (what captaincy is about) and over the horizon
    (what transfers are about), e.g. "projected 5.6 pts next GW, 24.8 over 5 GWs"."""
    if not projection.gameweeks:
        return "no projection"
    next_gw = projection.gameweeks[0]
    return (
        f"projected {next_gw.expected_points:.1f} pts next GW, "
        f"{projection.total:.1f} over {projection.horizon} GWs"
    )

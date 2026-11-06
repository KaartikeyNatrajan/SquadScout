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
    upcoming = _upcoming(m.next_fixtures)
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


def head_to_head(response: pb.GetHeadToHeadResponse) -> str:
    """Past meetings of two clubs, this season and up to 5 before it: the record,
    the newest 5 results and the top 5 scorers/assisters still at either club, e.g.:

    Arsenal v Liverpool (seasons: 2026-27, 2025-26, 2024-25, 2023-24, 2022-23, 2021-22)
    Arsenal won 4, drew 4 and lost 3 of 11 meetings (goals 18-17).
    Recent meetings:
      2024-25 2025-05-11: Liverpool 2-2 Arsenal (D). Goals: Gakpo, Diaz, Martinelli, Merino
      ...
    Players at either club today with goals or assists in these meetings:
      Saka (Arsenal, player id 16): 2 goals, 3 assists
      ...
    """
    seasons = ", ".join(response.seasons)
    # The note says which seasons are missing (e.g. a club wasn't in the league).
    # The newline is part of it, so no note leaves no blank line.
    note = f"\n{response.note}" if response.note else ""
    header = (
        f"{response.team_name} v {response.opponent_team_name} (seasons: {seasons})\n"
        f"{response.summary}{note}"
    )
    if not response.matches:
        return header

    # Both lists come sorted from the data service (newest first, most goals +
    # assists first); 5 of each is enough to answer and keeps the result short.
    matches = "\n".join("  " + _head_to_head_match(m) for m in response.matches[:5])
    players = "\n".join(
        f"  {p.web_name} ({p.team_name}, player id {p.player_id}): "
        f"{p.goals} goals, {p.assists} assists"
        for p in response.players[:5]
    )
    if not players:
        return f"{header}\nRecent meetings:\n{matches}"
    return (
        f"{header}\nRecent meetings:\n{matches}\n"
        f"Players at either club today with goals or assists in these meetings:\n{players}"
    )


def _head_to_head_match(m: pb.HeadToHeadMatch) -> str:
    """One meeting on one line, e.g.:

    2024-25 2025-05-11: Liverpool 2-2 Arsenal (D). Goals: Gakpo, Havertz x2

    The result (W/D/L) is from the requested team's side. Only goals are listed:
    assists are in the per-player totals, and every extra word is re-sent to the
    model on each later call.
    """
    line = (
        f"{m.season} {m.kickoff_time[:10]}: {m.home_team_name} {m.home_score}-"
        f"{m.away_score} {m.away_team_name} ({m.result})"
    )
    goals = ", ".join(
        e.player_name + (f" x{e.count}" if e.count > 1 else "")
        for e in m.events
        if e.kind == "goal"
    )
    return f"{line}. Goals: {goals}" if goals else line


def _upcoming(fixtures) -> str:
    """Next fixtures from a player's side, e.g. "Liverpool (A, 4), Hull City (H, 2)":
    the opponent, home or away, and the difficulty (1 easy - 5 hard)."""
    return ", ".join(
        f"{f.opponent_team_name} ({'H' if f.is_home else 'A'}, {f.difficulty})" for f in fixtures
    )


def _signed(x: float) -> str:
    """A points change with its sign, e.g. "+2.8" or "-1.0"."""
    return f"{x:+.1f}"


# ---------- A player's recent form (GetPlayerRecentForm) ----------


def player_form(response: pb.GetPlayerRecentFormResponse) -> str:
    """Totals over the last few gameweeks, then one line per gameweek, e.g.:

    Saka (player id 12), last 5 gameweeks: played in 4, 312 min, 28 pts (5.6 per gameweek),
    2 goals, 1 assists, 1 clean sheets, 3 bonus, xG 1.85, xA 0.90
    By gameweek, newest first:
      GW9 v Liverpool (A) 1-1: 90 min, 8 pts, 1 goal, 2 bonus, xG 0.61, xA 0.10
      GW8 v Hull City (H) 3-0: did not play
    """
    s = response.summary
    totals = (
        f"{response.web_name} (player id {response.player_id}), last {s.matches} gameweeks: "
        f"played in {s.appearances}, {s.minutes} min, {s.total_points} pts "
        f"({s.points_per_match:.1f} per gameweek), {s.goals_scored} goals, {s.assists} assists, "
        f"{s.clean_sheets} clean sheets, {s.bonus} bonus, "
        f"xG {s.expected_goals:.2f}, xA {s.expected_assists:.2f}"
    )
    if not response.matches:
        return f"{totals}\nNo gameweeks played yet."
    weeks = "\n".join("  " + _gameweek(g) for g in response.matches)
    return f"{totals}\nBy gameweek, newest first:\n{weeks}"


def _gameweek(g: pb.GameweekStats) -> str:
    """One gameweek on one line. Counting stats that are 0 are left out."""
    return f"GW{g.gameweek} {_opponents(g)}: {_gameweek_stats(g)}"


def _opponents(g: pb.GameweekStats) -> str:
    """Who he played, from his side: "v Liverpool (A) 1-1", or both matches of a
    double gameweek joined with " and "."""
    if g.opponent_team_name:  # exactly one match, side known
        return "v " + _versus(g.opponent_team_name, g.was_home, g)
    if g.match_results:  # a double gameweek, or a side we don't know
        return " and ".join(_match_result(m) for m in g.match_results)
    return "(match unknown)"


def _match_result(m: pb.MatchResult) -> str:
    if m.side_unknown:
        # He has moved club since; we don't know which side he was on (scores home-first).
        score = f" {m.team_score}-{m.opponent_score}" if m.HasField("team_score") else ""
        return f"a match on {m.kickoff_time[:10]}{score} (side unknown)"
    return "v " + _versus(m.opponent_team_name, m.was_home, m)


def _versus(opponent: str, home: bool, m) -> str:
    """ "Liverpool (A) 1-1"; the score is left out while the match is unplayed."""
    score = f" {m.team_score}-{m.opponent_score}" if m.HasField("team_score") else ""
    return f"{opponent} ({'H' if home else 'A'}){score}"


def _gameweek_stats(g: pb.GameweekStats) -> str:
    if g.minutes == 0:
        return "did not play"
    parts = [f"{g.minutes} min", f"{g.total_points} pts"]
    counts = [
        (g.goals_scored, "goal"),
        (g.assists, "assist"),
        (g.clean_sheets, "clean sheet"),
        (g.bonus, "bonus"),
        (g.saves, "save"),
        (g.yellow_cards, "yellow"),
        (g.red_cards, "red card"),
    ]
    parts += [f"{n} {word}" for n, word in counts if n]
    parts.append(f"xG {g.expected_goals:.2f}, xA {g.expected_assists:.2f}")
    return ", ".join(parts)


# ---------- A player's upcoming fixtures (GetPlayerFixtures) ----------


def player_fixtures(response: pb.GetPlayerFixturesResponse) -> str:
    """His next fixtures, with past meetings, e.g.:

    Saka (player id 12, Arsenal). FPL expected points for GW10: 5.2
      GW10 2026-11-07: Liverpool (away, difficulty 4). Past meetings: W2 D1 L1 ...
    """
    r = response
    header = f"{r.web_name} (player id {r.player_id}, {r.team_name})"
    if r.next_gameweek:
        header += f". FPL expected points for GW{r.next_gameweek}: {r.expected_points_next:.1f}"
    if not r.fixtures:
        return f"{header}\nNo fixtures left."
    lines = "\n".join("  " + _player_fixture(f) for f in r.fixtures)
    return f"{header}\n{lines}"


def _player_fixture(f: pb.PlayerFixture) -> str:
    when = f"GW{f.gameweek} {f.kickoff_time[:10]}" if f.gameweek else "not scheduled yet"
    line = (
        f"{when}: {f.opponent_team_name} "
        f"({'home' if f.is_home else 'away'}, difficulty {f.difficulty})"
    )
    return f"{line}. Past meetings: {f.head_to_head}" if f.head_to_head else line


# ---------- News (GetPlayerNews, GetTeamNews) ----------
# Code retrieves, the model interprets: topics are what a story discusses, not a
# verdict on the player, so they are labelled "about" and never turned into a status.


def player_news(response: pb.GetPlayerNewsResponse) -> str:
    """FPL's status for him, then the press stories, e.g.:

    Saka (player id 12, Arsenal): doubtful. News: Knee injury - 75% chance of playing (FPL, 2026-11-03)
    Press stories:
      3 hours ago, BBC Sport (about: injury, press_conference): Arteta on Saka's knee...
    """
    p = response.player
    status = f"{p.web_name} (player id {p.id}, {p.team_name}): {_availability(p)}"
    if response.fpl_news_added:
        status += f" (FPL, {response.fpl_news_added[:10]})"
    return status + _news_items(response.items, response.note)


def team_news(response: pb.GetTeamNewsResponse) -> str:
    """Press stories about a club, in the same form as player_news."""
    header = f"{response.team_name} (team id {response.team_id}) news"
    return header + _news_items(response.items, response.note)


def _news_items(items, note: str) -> str:
    """The stories as indented lines, plus the coverage note; starts with a newline."""
    text = ""
    if items:
        text += "\nPress stories:\n" + "\n".join("  " + _news_item(i) for i in items)
    if note:
        text += f"\n{note}"
    return text


def _news_item(i: pb.NewsItem) -> str:
    """One story: when, who, what it's about and the snippet. The URL is left out:
    the model can't open it, and it costs tokens on every later call."""
    about = f" (about: {', '.join(i.topics)})" if i.topics else ""
    # Headlines often have no full stop, so the "also mentions" part gets its own separator.
    also = f" | also mentions: {', '.join(i.also_mentions)}" if i.also_mentions else ""
    return f"{i.age}, {i.source}{about}: {i.snippet or i.title}{also}"


# ---------- Players with their outlook (FindPlayers, GetTransferTrends) ----------


def find_players(response: pb.FindPlayersResponse) -> str:
    """One line per player, best first (see player_outlook)."""
    if not response.players:
        return "No players match."
    return "\n".join(player_outlook(o) for o in response.players)


def transfer_trends(response: pb.GetTransferTrendsResponse) -> str:
    """The most transferred players, with what the crowd is doing and the price outlook."""
    if not response.players:
        return "No transfer data yet."
    players = "\n".join("  " + player_outlook(o, crowd=True) for o in response.players)
    return f"Transfers for gameweek {response.gameweek}, most first:\n{players}"


def player_outlook(o: pb.PlayerOutlook, crowd: bool = False) -> str:
    """A player judged as a transfer target, on one line, e.g.:

    Palmer (player id 210, Chelsea), MID £10.5m: available; form 7.2; 68 pts;
    owned by 41.2%; projected 6.1 pts next GW, 27.0 over 5 GWs; next: Hull City (H, 2), ...

    crowd=True adds what other managers are doing and the price outlook.
    """
    p = o.player
    line = (
        f"{p.web_name} (player id {p.id}, {p.team_name}), "
        f"{POSITION_SHORT.get(p.position, '?')} £{p.price:.1f}m: {_availability(p)}; "
        f"form {p.form}; {p.total_points} pts; owned by {p.selected_by_percent}%; "
        f"{_projected(o.projection)}; next: {_upcoming(o.next_fixtures) or 'none'}"
    )
    if crowd:
        line += f"; {p.transfer_sentiment}; price: {p.price_change_outlook}"
    return line


# ---------- Transfers (EvaluateTransfers, SuggestTransfers) ----------


def evaluate_transfers(response: pb.EvaluateTransfersResponse) -> str:
    """Whether the transfers are allowed, what they cost, and the points they gain, e.g.:

    Allowed.
    2 transfers, 1 free: 4-point hit. Bank £1.1m before, £0.3m after.
    Over the next 5 GWs (our projection; weigh any hit against this): players out 38.2 pts,
    in 45.0 pts; the best starting XI gains +2.8 after the hit.
    Next GW only (FPL's expected points, ignores who starts): out 9.0, in 11.0, net -2.0.
    Out:
      <player_outlook>
    In:
      <player_outlook>
    """
    r = response
    verdict = "Allowed." if r.valid else "Not allowed: " + "; ".join(r.violations)
    hit = f"{r.hit}-point hit" if r.hit else "no hit"
    cost = (
        f"{r.transfers} transfer(s), {r.free_transfers} free: {hit}. "
        f"Bank {_money(r.bank_before)} before, {_money(r.bank_after)} after."
    )
    horizon = (
        f"Over the next {r.horizon} GWs (our projection; weigh any hit against this): "
        f"players out {r.projected_points_out:.1f} pts, in {r.projected_points_in:.1f} pts; "
        f"the best starting XI changes by {_signed(r.projected_points_net)} after the hit."
    )
    next_gw = (
        f"Next GW only (FPL's expected points, ignores who starts): "
        f"out {r.expected_points_out:.1f}, in {r.expected_points_in:.1f}, "
        f"net {_signed(r.expected_points_net)}."
    )
    out = "\n".join("  " + player_outlook(o) for o in r.players_out)
    in_ = "\n".join("  " + player_outlook(o) for o in r.players_in)
    text = f"{verdict}\n{cost}\n{horizon}\n{next_gw}\nOut:\n{out}\nIn:\n{in_}"
    # The data service's first note explains its JSON field names (expected_points_*),
    # which the lines above already say in words; the model never sees those names.
    notes = [n for n in r.notes if not n.startswith("expected_points_*")]
    if notes:
        text += "\nNotes: " + " ".join(notes)
    return text


def suggest_transfers(response: pb.SuggestTransfersResponse) -> str:
    """The best legal transfer sets, best first, e.g.:

    Best transfers over the next 5 GWs (gain = change in the best starting XI's
    projected points, after any hit):
    1. Out Saka (player id 12, Arsenal, MID, sells £9.5m, 20.1 pts) -> in Palmer
       (player id 210, Chelsea, MID, £10.5m, 26.0 pts). Gain +5.9, no hit, bank after £0.1m.
    """
    r = response
    notes = ("\nNotes: " + " ".join(r.notes)) if r.notes else ""
    if not r.suggestions:
        return f"No transfer is worth making within the limits given.{notes}"
    header = (
        f"Best transfers over the next {r.horizon} GWs (gain = change in the best "
        f"starting XI's projected points, after any hit):"
    )
    # The top 3 are enough to recommend from, and each set of two transfers is
    # ~350 characters; later ones mostly repeat the same players.
    best = r.suggestions[:MAX_SUGGESTIONS]
    lines = "\n".join(f"{n}. {_suggestion(s)}" for n, s in enumerate(best, start=1))
    return f"{header}\n{lines}{notes}"


MAX_SUGGESTIONS = 3


def _money(x: float) -> str:
    """£m with the sign in front: "£1.1m", or "-£3.4m" when unaffordable."""
    return f"-£{-x:.1f}m" if x < 0 else f"£{x:.1f}m"


def _suggestion(s: pb.TransferSuggestion) -> str:
    out = ", ".join(_transfer_player(p, "sells ") for p in s.players_out)
    in_ = ", ".join(_transfer_player(p) for p in s.players_in)
    hit = f"{s.hit}-point hit" if s.hit else "no hit"
    return (
        f"Out {out} -> in {in_}. Gain {_signed(s.expected_points_gain)}, {hit}, "
        f"bank after {_money(s.bank_after)}."
    )


def _transfer_player(p: pb.TransferPlayer, price_label: str = "") -> str:
    return (
        f"{p.web_name} (player id {p.id}, {p.team_name}, "
        f"{POSITION_SHORT.get(p.position, '?')}, {price_label}£{p.price:.1f}m, "
        f"{p.projected_points:.1f} pts)"
    )

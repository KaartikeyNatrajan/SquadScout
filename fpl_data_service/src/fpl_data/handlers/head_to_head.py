"""GetHeadToHead, and the one-line head-to-head summaries on fixtures."""

from fpl_data import head_to_head
from fpl_data.db import players, results, teams
from fpl_data.db.teams import TeamRow
from fpl_data.handlers.base import ServiceBase
from fpl_data.handlers.errors import invalid_argument
from fpl_data.history import seasons as season_labels
from fpl_data.history.results import Result
from squadscout.v1 import fpl_pb2 as pb

MAX_PLAYERS = 10  # HeadToHeadPlayer entries returned


class SummaryLines:
    """One-line head-to-head summaries for fixture lists. Make one per request:
    it looks up the clubs' lasting codes once."""

    def __init__(self, store):
        self._store = store
        self._codes = {t.id: t.code for t in teams.all_teams(store)}
        self._seasons = season_labels.searched(store)

    def line(self, team_id: int, opponent_id: int) -> str:
        """The record of `team_id` against `opponent_id`, from `team_id`'s side."""
        team, opponent = self._codes.get(team_id), self._codes.get(opponent_id)
        if team is None or opponent is None:
            return ""
        meetings = results.meetings(self._store, team, opponent, self._seasons)
        return head_to_head.summary(team, meetings, self._seasons[-1])


class HeadToHeadHandlers(ServiceBase):
    async def get_head_to_head(self, request: pb.GetHeadToHeadRequest) -> pb.GetHeadToHeadResponse:
        """Past Premier League meetings of two clubs, from the first club's side."""
        if request.seasons < 0:
            raise invalid_argument("seasons can't be negative")
        team = self.lookup_team(request.team_id, request.team_name)
        opponent = self.lookup_team(request.opponent_team_id, request.opponent_team_name)
        if team.id == opponent.id:
            raise invalid_argument(f"both teams are {team.name}; give two different clubs")
        past = min(request.seasons or season_labels.PAST_SEASONS, season_labels.PAST_SEASONS)
        searched = season_labels.searched(self.store, past)

        meetings = results.meetings(self.store, team.code, opponent.code, searched)
        rec = head_to_head.record(team.code, meetings)
        ids = results.player_ids_by_code(
            self.store, sorted({e.player_code for m in meetings for e in m.events})
        )
        return pb.GetHeadToHeadResponse(
            team_id=team.id,
            team_name=team.name,
            opponent_team_id=opponent.id,
            opponent_team_name=opponent.name,
            seasons=searched,
            record=pb.HeadToHeadRecord(**vars(rec)),
            summary=head_to_head.summary(team.code, meetings, searched[-1]),
            matches=[_match(team.code, m, ids) for m in meetings],
            players=self._players(meetings, ids, {team.id, opponent.id}),
            note=self._coverage_note(team, opponent, searched),
        )

    def _players(
        self, meetings: list[Result], ids: dict[int, int], clubs: set[int]
    ) -> list[pb.HeadToHeadPlayer]:
        """Today's players at either club who scored or assisted in these meetings."""
        totals = head_to_head.contributions(meetings)
        code_of = {player_id: code for code, player_id in ids.items()}  # today's id -> lasting code
        rows = players.players_by_id(self.store, [ids[code] for code in totals if code in ids])
        found = [
            pb.HeadToHeadPlayer(
                player_id=p.id,
                web_name=p.web_name,
                team_name=p.team_name,
                goals=totals[code_of[p.id]][0],
                assists=totals[code_of[p.id]][1],
            )
            for p in rows.values()
            if p.team_id in clubs
        ]
        found.sort(key=lambda x: (-(x.goals + x.assists), -x.goals, x.web_name))
        return found[:MAX_PLAYERS]

    def _coverage_note(self, team: TeamRow, opponent: TeamRow, searched: list[str]) -> str:
        """What the record doesn't cover, so a short record isn't mistaken for a full
        one: past seasons not downloaded yet, and seasons a club wasn't in the Premier
        League. (The current season is still being played, so it's never "missing".)"""
        past = searched[1:]
        have = [s for s in past if results.has_season(self.store, s)]
        notes = []
        if len(have) < len(past):
            missing = [s for s in past if s not in have]
            notes.append(f"results for {', '.join(missing)} are not available yet")
        for club in (team, opponent):
            played = results.seasons_played(self.store, club.code, have)
            absent = [s for s in have if s not in played]
            if absent:
                notes.append(f"{club.name} were not in the Premier League in {', '.join(absent)}")
        return "; ".join(notes)


def _match(team_code: int, m: Result, ids: dict[int, int]) -> pb.HeadToHeadMatch:
    names = {m.home.code: m.home.name, m.away.code: m.away.name}
    return pb.HeadToHeadMatch(
        season=m.season,
        kickoff_time=m.kickoff_time,
        home_team_name=m.home.name,
        away_team_name=m.away.name,
        home_score=m.home_score,
        away_score=m.away_score,
        team_at_home=m.home.code == team_code,
        result=head_to_head.outcome(team_code, m),
        events=[
            pb.MatchEvent(
                kind=e.kind,
                player_name=e.player_name,
                team_name=names.get(e.team_code, ""),
                count=e.count,
                player_id=ids.get(e.player_code, 0),
            )
            for e in m.events
        ],
    )

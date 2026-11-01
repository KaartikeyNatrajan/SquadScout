"""Head-to-head records (pure) and the GetHeadToHead RPC."""

import pytest

from fpl_data import head_to_head
from fpl_data.history.archive import ArchiveClient
from fpl_data.history.refresh import HistoryRefresher, HistoryRefreshError
from fpl_data.history.results import Club, MatchEvent, Result
from squadscout.v1 import fpl_pb2 as pb
from tests.fake_archive import FakeArchive
from tests.helpers import error_of

ARS, LIV = Club(3, "Arsenal"), Club(14, "Liverpool")


def result(home, away, hs, as_, when, events=()):
    return Result("2025-26", 1, when, home, away, hs, as_, list(events))


MEETINGS = [  # newest first
    result(ARS, LIV, 0, 0, "2026-01-08T20:00:00Z"),
    result(LIV, ARS, 1, 0, "2025-08-31T15:30:00Z", [MatchEvent("goal", 14, 7, "Szoboszlai", 1)]),
    result(
        ARS,
        LIV,
        3,
        1,
        "2025-03-01T15:00:00Z",
        [MatchEvent("goal", 3, 1, "Saka", 2), MatchEvent("assist", 3, 2, "Rice", 1)],
    ),
]


def test_record_from_each_side():
    assert vars(head_to_head.record(3, MEETINGS)) == dict(
        played=3, wins=1, draws=1, losses=1, goals_for=3, goals_against=2
    )
    assert head_to_head.record(14, MEETINGS).wins == 1
    assert [head_to_head.outcome(14, m) for m in MEETINGS] == ["D", "W", "L"]


def test_summary_line():
    assert head_to_head.summary(3, MEETINGS, "2021-22") == (
        "last 3 PL meetings since 2021-22: W1 D1 L1, goals 3-2; last: D 0-0 at home (2026-01-08)"
    )
    assert head_to_head.summary(14, MEETINGS[1:2], "2021-22").startswith("last 1 PL meeting since")
    assert head_to_head.summary(3, [], "2021-22") == "no Premier League meetings since 2021-22"


def test_contributions_count_goals_and_assists_per_player():
    assert head_to_head.contributions(MEETINGS) == {7: (1, 0), 1: (2, 0), 2: (0, 1)}


# ----- the RPC, on the saved test data: this season from the fake FPL API, 2024-25
# and 2025-26 from the fake archive -----


@pytest.fixture
async def h2h_service(service):
    archive = ArchiveClient("http://archive.test", transport=FakeArchive().transport)
    with pytest.raises(HistoryRefreshError):  # 3 of the 5 past seasons aren't in the fake
        await HistoryRefresher(service.store, archive).refresh()
    await archive.aclose()
    return service


async def test_arsenal_v_liverpool(h2h_service):
    r = await h2h_service.get_head_to_head(
        pb.GetHeadToHeadRequest(team_name="Arsenal", opponent_team_name="Liverpool")
    )

    assert (r.team_name, r.opponent_team_name) == ("Arsenal", "Liverpool")
    assert list(r.seasons) == ["2026-27", "2025-26", "2024-25", "2023-24", "2022-23", "2021-22"]
    # Newest first: 0-0, 0-1 (away), 2-2 (away), 2-2 (home).
    assert [
        (m.season, m.result, m.home_score, m.away_score, m.team_at_home) for m in r.matches
    ] == [
        ("2025-26", "D", 0, 0, True),
        ("2025-26", "L", 1, 0, False),
        ("2024-25", "D", 2, 2, False),
        ("2024-25", "D", 2, 2, True),
    ]
    assert (r.record.played, r.record.wins, r.record.draws, r.record.losses) == (4, 0, 3, 1)
    assert (r.record.goals_for, r.record.goals_against) == (4, 5)
    assert r.summary.startswith("last 4 PL meetings since 2021-22: W0 D3 L1, goals 4-5")
    assert "2023-24, 2022-23, 2021-22 are not available yet" in r.note

    anfield = r.matches[2]  # Liverpool 2-2 Arsenal, May 2025
    events = {(e.kind, e.player_name, e.team_name) for e in anfield.events}
    assert ("red_card", "Merino", "Arsenal") in events
    assert ("goal", "Gakpo", "Liverpool") in events


async def test_the_players_today_who_scored_or_assisted(h2h_service):
    r = await h2h_service.get_head_to_head(pb.GetHeadToHeadRequest(team_id=1, opponent_team_id=14))

    by_name = {p.web_name: (p.goals, p.assists, p.team_name) for p in r.players}
    assert by_name["Saka"] == (1, 0, "Arsenal")  # scored in the 2-2 at home, Oct 2024
    assert all(p.team_name in ("Arsenal", "Liverpool") for p in r.players)
    # Most goals + assists first.
    totals = [p.goals + p.assists for p in r.players]
    assert totals == sorted(totals, reverse=True)


async def test_this_seasons_meeting_counts_and_absent_seasons_are_noted(h2h_service):
    r = await h2h_service.get_head_to_head(
        pb.GetHeadToHeadRequest(team_name="Arsenal", opponent_team_name="Sunderland")
    )

    # Sunderland 0-2 Arsenal this season, plus 2-2 and 3-0 in 2025-26.
    assert [(m.season, m.result) for m in r.matches] == [
        ("2026-27", "W"),
        ("2025-26", "W"),
        ("2025-26", "D"),
    ]
    assert "Sunderland were not in the Premier League in 2024-25" in r.note


async def test_seasons_limits_the_search(h2h_service):
    r = await h2h_service.get_head_to_head(
        pb.GetHeadToHeadRequest(team_id=1, opponent_team_id=14, seasons=1)
    )
    assert list(r.seasons) == ["2026-27", "2025-26"]
    assert {m.season for m in r.matches} == {"2025-26"}


@pytest.mark.parametrize(
    ("request_fields", "message"),
    [
        (dict(team_id=1, opponent_team_id=1), "two different clubs"),
        (dict(team_id=1, opponent_team_id=14, seasons=-1), "can't be negative"),
        (dict(team_id=1, opponent_team_name="Barcelona"), 'no team called "Barcelona"'),
    ],
)
async def test_bad_requests(service, request_fields, message):
    err = await error_of(service.get_head_to_head(pb.GetHeadToHeadRequest(**request_fields)))
    assert message in err.message


async def test_fixtures_carry_a_head_to_head_line(h2h_service):
    fixtures = (await h2h_service.get_fixtures(pb.GetFixturesRequest(team_name="Arsenal"))).fixtures
    v_liverpool = next(
        f for f in fixtures if {f.home_team_name, f.away_team_name} == {"Arsenal", "Liverpool"}
    )
    # From the home team's side: Liverpool host Arsenal in gameweek 9.
    assert v_liverpool.head_to_head.startswith(
        "last 4 PL meetings since 2021-22: W1 D3 L0, goals 5-4"
    )

    player = await h2h_service.get_player_fixtures(
        pb.GetPlayerFixturesRequest(player_id=12, next_n=10)
    )
    at_anfield = next(f for f in player.fixtures if f.opponent_team_name == "Liverpool")
    assert at_anfield.head_to_head.startswith("last 4 PL meetings since 2021-22: W0 D3 L1")


async def test_no_meetings_says_so(service):
    # No past seasons downloaded and no meeting this season yet.
    r = await service.get_head_to_head(pb.GetHeadToHeadRequest(team_id=1, opponent_team_id=14))
    assert list(r.matches) == []
    assert r.summary == "no Premier League meetings since 2021-22"

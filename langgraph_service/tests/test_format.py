"""Compact tool results (agent/format.py). Hand-built protobuf messages, no servers."""

from agent import format
from squadscout.v1 import fpl_pb2 as pb


def saka(**overrides) -> pb.Player:
    fields = {
        "id": 12,
        "web_name": "Saka",
        "team_id": 1,
        "team_name": "Arsenal",
        "position": pb.POSITION_MIDFIELDER,
        "price": 9.5,
        "total_points": 37,
        "points_per_game": 5.3,
        "form": 1.2,
        "starts": 7,
        "minutes": 596,
        "goals_scored": 3,
        "assists": 0,
        "clean_sheets": 4,
        "bonus": 4,
        "expected_goals": 3.63,
        "expected_assists": 1.2,
        "expected_points_next": 0.0,
        "selected_by_percent": 6.5,
        "availability": "injured",
        "news": "Thigh injury - Unknown return date",
        "chance_of_playing_next_round": 0,
        "transfer_sentiment": "being sold: net -0.28% of all managers this gameweek",
        "price_change_outlook": "no price change expected within 3 price updates",
    }
    return pb.Player(**(fields | overrides))


def test_player_keeps_ids_and_the_facts_that_matter():
    text = format.player(saka())

    assert text.splitlines()[0] == "Saka (player id 12): Arsenal (team id 1), midfielder, £9.5m"
    assert (
        "availability: injured. News: Thigh injury - Unknown return date. "
        "0% chance of playing next round" in text
    )
    assert "37 pts (5.3 per game), form 1.2" in text
    assert "3 goals, 0 assists" in text  # zeros are shown, not dropped
    assert "being sold" in text and "no price change expected" in text


def test_chance_of_playing_is_left_out_when_fpl_has_no_concerns():
    fit = saka(availability="available", news="")
    fit.ClearField("chance_of_playing_next_round")  # unset = no concerns (0 would mean "out")

    assert "availability: available\n" in format.player(fit)


def test_player_text_is_much_shorter_than_the_json_tools_used_to_send():
    from agent.tools import _to_json  # the JSON dump search_players returned before

    response = pb.SearchPlayersResponse(players=[saka()])

    assert len(format.players(response)) < len(_to_json(response)) / 2


def test_no_players():
    assert format.players(pb.SearchPlayersResponse()) == "No players found with that name."


def test_fixture_line_with_head_to_head():
    f = pb.Fixture(
        gameweek=10,
        kickoff_time="2026-11-07T15:00:00Z",
        home_team_name="Arsenal",
        away_team_name="Hull City",
        home_difficulty=2,
        away_difficulty=5,
        head_to_head="no Premier League meetings since 2021-22",
    )

    assert format.fixture(f) == (
        "GW10 2026-11-07 15:00 UTC: Arsenal (home, difficulty 2) v Hull City (away, difficulty 5)"
        ". Past meetings, from Arsenal's side: no Premier League meetings since 2021-22"
    )


def test_fixture_scores_show_whether_finished():
    live = pb.Fixture(
        gameweek=9, home_team_name="Liverpool", away_team_name="Arsenal", home_score=0, away_score=1
    )

    assert "in progress: 0-1" in format.fixture(live)
    live.finished = True
    assert "result: 0-1" in format.fixture(live)
    assert "date not set" in format.fixture(live)


def squad_member(position: int, web_name: str, **fields) -> pb.SquadPlayer:
    return pb.SquadPlayer(
        player=pb.Player(
            id=position,
            web_name=web_name,
            team_name="Arsenal",
            position=pb.POSITION_FORWARD,
            availability="available",
            form=5.0,
        ),
        squad_position=position,
        selling_price=7.0,
        projection=pb.Projection(
            horizon=5,
            total=21.0,
            gameweeks=[pb.GameweekProjection(gameweek=10, expected_points=4.5)],
        ),
        next_fixtures=[
            pb.PlayerFixture(opponent_team_name="Hull City", is_home=True, difficulty=2)
        ],
        **fields,
    )


def test_manager_team_summary():
    team = pb.GetManagerTeamResponse(
        team_id=6,
        team_name="Bol Uwd Utd",
        manager_name="Osian",
        overall_points=637,
        overall_rank=64895,
        squad_gameweek=9,
        next_gameweek=10,
        next_deadline="2026-11-06T18:30:00Z",
        bank=1.1,
        squad_value=101.1,
        free_transfers=1,
        chips_available=["freehit"],
        notes=["Squad as of the last deadline."],
        players=[squad_member(1, "Starter", is_captain=True), squad_member(12, "Sub")],
    )

    lines = format.manager_team(team).splitlines()

    assert lines[0] == "Bol Uwd Utd (team id 6), manager Osian: 637 points, overall rank 64,895."
    assert "deadline 2026-11-06 18:30 UTC" in lines[1]
    assert lines[2] == "Bank £1.1m, squad value £101.1m, 1 free transfer(s), chips left: freehit."
    assert lines[3] == "Starting XI:"
    assert lines[4] == (
        "  FWD Starter (captain) (player id 1, Arsenal) sells £7.0m: available; form 5.0; "
        "projected 4.5 pts next GW, 21.0 over 5 GWs; next: Hull City (H, 2)"
    )
    assert lines[5] == "Bench, in substitution order:"  # squad_position 12 starts the bench
    assert lines[6].startswith("  FWD Sub (player id 12")
    assert lines[7] == "Notes: Squad as of the last deadline."


def test_a_full_chance_of_playing_is_not_repeated():
    p = saka(availability="available", news="", chance_of_playing_next_round=100)

    assert "availability: available\n" in format.player(p)

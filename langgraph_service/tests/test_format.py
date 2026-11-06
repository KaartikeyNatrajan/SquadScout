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


# ---------- Head to head ----------


def h2h_match(**fields) -> pb.HeadToHeadMatch:
    defaults = {
        "season": "2024-25",
        "kickoff_time": "2025-05-11T15:30:00Z",
        "home_team_name": "Liverpool",
        "away_team_name": "Arsenal",
        "result": "D",
    }
    return pb.HeadToHeadMatch(**(defaults | fields))


def test_head_to_head_match_lists_only_goals():
    m = h2h_match(
        home_score=2,
        away_score=2,
        events=[
            pb.MatchEvent(kind="goal", player_name="Gakpo", count=1),
            pb.MatchEvent(kind="assist", player_name="Salah", count=1),
            pb.MatchEvent(kind="goal", player_name="Havertz", count=2),
        ],
    )

    assert format._head_to_head_match(m) == (
        "2024-25 2025-05-11: Liverpool 2-2 Arsenal (D). Goals: Gakpo, Havertz x2"
    )


def test_goalless_match_has_no_goals_part():
    assert (
        format._head_to_head_match(h2h_match()) == "2024-25 2025-05-11: Liverpool 0-0 Arsenal (D)"
    )


def test_head_to_head_keeps_the_newest_five_meetings_and_player_ids():
    response = pb.GetHeadToHeadResponse(
        team_name="Arsenal",
        opponent_team_name="Liverpool",
        seasons=["2025-26", "2024-25"],
        summary="W3 D5 L3",
        matches=[h2h_match(season=f"s{n}") for n in range(7)],
        players=[pb.HeadToHeadPlayer(player_id=12, web_name="Saka", team_name="Arsenal", goals=4)],
    )

    lines = format.head_to_head(response).splitlines()

    assert lines[:3] == [
        "Arsenal v Liverpool (seasons: 2025-26, 2024-25)",
        "W3 D5 L3",
        "Recent meetings:",
    ]
    assert [line[2:4] for line in lines[3:8]] == ["s0", "s1", "s2", "s3", "s4"]
    assert lines[8].startswith("Players at either club today")
    assert lines[9] == "  Saka (Arsenal, player id 12): 4 goals, 0 assists"


def test_head_to_head_without_meetings_shows_the_note_and_no_blank_lines():
    response = pb.GetHeadToHeadResponse(
        team_name="Arsenal",
        opponent_team_name="Sunderland",
        seasons=["2025-26"],
        summary="No meetings.",
        note="Sunderland were not in the Premier League in 2024-25",
    )

    assert format.head_to_head(response) == (
        "Arsenal v Sunderland (seasons: 2025-26)\nNo meetings.\n"
        "Sunderland were not in the Premier League in 2024-25"
    )
    response.note = ""
    assert format.head_to_head(response) == "Arsenal v Sunderland (seasons: 2025-26)\nNo meetings."


# ---------- Recent form ----------


def test_player_form_totals_and_one_line_per_gameweek():
    response = pb.GetPlayerRecentFormResponse(
        player_id=12,
        web_name="Saka",
        summary=pb.FormSummary(
            matches=2, appearances=1, minutes=90, total_points=8, points_per_match=4.0
        ),
        matches=[
            pb.GameweekStats(
                gameweek=9,
                opponent_team_name="Liverpool",
                team_score=1,
                opponent_score=1,
                minutes=90,
                total_points=8,
                goals_scored=1,
                expected_goals=0.61,
            ),
            pb.GameweekStats(
                gameweek=8,
                opponent_team_name="Everton",
                was_home=True,
                team_score=2,
                opponent_score=0,
            ),
        ],
    )

    lines = format.player_form(response).splitlines()

    assert lines[0].startswith(
        "Saka (player id 12), last 2 gameweeks: played in 1, 90 min, 8 pts (4.0 per gameweek)"
    )
    assert lines[2] == "  GW9 v Liverpool (A) 1-1: 90 min, 8 pts, 1 goal, xG 0.61, xA 0.00"
    assert lines[3] == "  GW8 v Everton (H) 2-0: did not play"


def test_double_gameweek_names_both_opponents():
    g = pb.GameweekStats(
        gameweek=26,
        minutes=180,
        total_points=12,
        match_results=[
            pb.MatchResult(
                opponent_team_name="Hull City", was_home=True, team_score=3, opponent_score=0
            ),
            pb.MatchResult(opponent_team_name="Leeds", team_score=1, opponent_score=1),
        ],
    )

    assert format._gameweek(g).startswith("GW26 v Hull City (H) 3-0 and v Leeds (A) 1-1: 180 min")


# ---------- News ----------


def test_player_news_labels_topics_as_what_a_story_is_about():
    response = pb.GetPlayerNewsResponse(
        player=saka(),
        fpl_news_added="2026-10-21T10:00:00Z",
        items=[
            pb.NewsItem(
                age="4 hours ago",
                source="BBC Sport",
                snippet="Saka remains out",
                url="https://example.com/story",
                topics=["injury"],
                also_mentions=["Konsa"],
            )
        ],
        note="1 of 4 stories shown",
    )

    text = format.player_news(response)

    assert text.splitlines() == [
        (
            "Saka (player id 12, Arsenal): injured. News: Thigh injury - Unknown return date. "
            "0% chance of playing next round (FPL, 2026-10-21)"
        ),
        "Press stories:",
        "  4 hours ago, BBC Sport (about: injury): Saka remains out | also mentions: Konsa",
        "1 of 4 stories shown",
    ]
    assert "https://" not in text


def test_team_news_without_stories():
    response = pb.GetTeamNewsResponse(
        team_id=1, team_name="Arsenal", note="no press stories in the last 72 hours"
    )

    assert (
        format.team_news(response)
        == "Arsenal (team id 1) news\nno press stories in the last 72 hours"
    )


# ---------- Players with their outlook ----------


def outlook(**player_fields) -> pb.PlayerOutlook:
    return pb.PlayerOutlook(
        player=saka(**player_fields),
        projection=pb.Projection(
            horizon=5, total=20.0, gameweeks=[pb.GameweekProjection(expected_points=4.0)]
        ),
        next_fixtures=[
            pb.PlayerFixture(opponent_team_name="Hull City", is_home=True, difficulty=2)
        ],
    )


def test_find_players_one_line_each():
    text = format.find_players(
        pb.FindPlayersResponse(players=[outlook(), outlook(id=13, web_name="Rice")])
    )

    lines = text.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("Saka (player id 12, Arsenal), MID £9.5m: injured.")
    assert lines[0].endswith("projected 4.0 pts next GW, 20.0 over 5 GWs; next: Hull City (H, 2)")
    assert format.find_players(pb.FindPlayersResponse()) == "No players match."


def test_transfer_trends_add_what_managers_are_doing():
    text = format.transfer_trends(pb.GetTransferTrendsResponse(gameweek=10, players=[outlook()]))

    assert text.splitlines()[0] == "Transfers for gameweek 10, most first:"
    assert "being sold: net -0.28%" in text
    assert "price: no price change expected" in text


# ---------- Transfers ----------


def test_evaluate_transfers_not_allowed_and_unaffordable():
    response = pb.EvaluateTransfersResponse(
        valid=False,
        violations=["the squad would have 4 Arsenal players; the limit is 3"],
        transfers=2,
        free_transfers=1,
        hit=4,
        bank_before=1.1,
        bank_after=-3.4,
        horizon=5,
        projected_points_out=21.4,
        projected_points_in=30.0,
        projected_points_net=2.8,
        expected_points_net=-1.0,
        players_out=[outlook()],
        players_in=[outlook(id=13, web_name="Rice")],
        notes=["expected_points_* are FPL's projection...", "Squad as of the last deadline."],
    )

    lines = format.evaluate_transfers(response).splitlines()

    assert lines[0] == "Not allowed: the squad would have 4 Arsenal players; the limit is 3"
    assert lines[1] == "2 transfer(s), 1 free: 4-point hit. Bank £1.1m before, -£3.4m after."
    assert lines[2].endswith("the best starting XI changes by +2.8 after the hit.")
    assert lines[3].endswith("net -1.0.")
    assert lines[4] == "Out:" and lines[6] == "In:"
    # The note explaining JSON field names is dropped; real caveats stay.
    assert lines[8] == "Notes: Squad as of the last deadline."


def test_suggest_transfers_keeps_the_best_three():
    def suggestion(gain: float) -> pb.TransferSuggestion:
        return pb.TransferSuggestion(
            expected_points_gain=gain,
            bank_after=0.5,
            players_out=[
                pb.TransferPlayer(
                    id=12,
                    web_name="Saka",
                    team_name="Arsenal",
                    position=pb.POSITION_MIDFIELDER,
                    price=9.5,
                    projected_points=20.1,
                )
            ],
            players_in=[
                pb.TransferPlayer(
                    id=210,
                    web_name="Palmer",
                    team_name="Chelsea",
                    position=pb.POSITION_MIDFIELDER,
                    price=10.5,
                    projected_points=26.0,
                )
            ],
        )

    response = pb.SuggestTransfersResponse(
        horizon=5, suggestions=[suggestion(g) for g in (5.9, 4.0, 3.0, 2.0)]
    )

    lines = format.suggest_transfers(response).splitlines()

    assert len(lines) == 4  # header + 3
    assert lines[1] == (
        "1. Out Saka (player id 12, Arsenal, MID, sells £9.5m, 20.1 pts) -> in Palmer "
        "(player id 210, Chelsea, MID, £10.5m, 26.0 pts). Gain +5.9, no hit, bank after £0.5m."
    )


def test_no_transfer_worth_making():
    response = pb.SuggestTransfersResponse(horizon=5, notes=["Projections are rough estimates."])

    assert format.suggest_transfers(response) == (
        "No transfer is worth making within the limits given.\nNotes: Projections are rough estimates."
    )

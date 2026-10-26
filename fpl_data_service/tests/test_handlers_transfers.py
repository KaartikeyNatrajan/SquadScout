import grpc
import pytest

from fpl_data.handlers.base import DEFAULT_LIMIT
from squadscout.v1 import fpl_pb2 as pb
from tests.helpers import error_of

# Players used below. Team 6 owns the first four; it already has 3 Arsenal and 3
# Chelsea players, £0.1m in the bank and 1 free transfer.
GROSS = 124  # MID, Brighton, sells for £5.6m, expected 11.2 points
WISSA = 464  # FWD, sells for £6.1m
MUHAREMOVIC = 334  # DEF, sells for £5.0m
RAYA = 1  # GK
BELLOUMI = 286  # MID, Hull, £5.1m, expected 7.0
KOSTOULAS = 138  # FWD, Brighton, £5.6m
BOGLE = 330  # DEF, Leeds, £4.6m
SAKA = 12  # MID, Arsenal, £9.5m


async def evaluate(service, out, into, **fields) -> pb.EvaluateTransfersResponse:
    request = pb.EvaluateTransfersRequest(
        team_id=6, player_out_ids=out, player_in_ids=into, **fields
    )
    return await service.evaluate_transfers(request)


def has_violation(response, text: str) -> bool:
    return any(text in v for v in response.violations)


async def test_a_valid_transfer(service):
    # Groß out (sells for £5.6m), Belloumi in (£5.1m): £0.1m + 5.6 - 5.1 = £0.6m left.
    r = await evaluate(service, [GROSS], [BELLOUMI])

    assert r.valid
    assert list(r.violations) == []
    assert (r.transfers, r.hit, r.bank_before, r.bank_after) == (1, 0, 0.1, 0.6)
    # 7.0 expected in, 11.2 out: a bad swap on next week's numbers.
    assert (r.expected_points_in, r.expected_points_out, r.expected_points_net) == (7.0, 11.2, -4.2)
    assert len(r.players_in) == 1
    assert len(r.players_in[0].next_fixtures) == 3


async def test_saka_for_gross_breaks_two_rules(service):
    r = await evaluate(service, [GROSS], [SAKA])

    assert not r.valid
    # £0.1m + 5.6 - 9.5 = -£3.8m.
    assert has_violation(r, "£3.8m more than the budget")
    assert r.bank_after == -3.8
    assert has_violation(r, "4 Arsenal players")


async def test_hits_and_max_hit(service):
    # Three transfers with 1 free: two extra, an 8-point hit. Affordable:
    # £0.1m + (5.6 + 6.1 + 5.0) - (5.1 + 5.6 + 4.6) = £1.5m left.
    out, into = [GROSS, WISSA, MUHAREMOVIC], [BELLOUMI, KOSTOULAS, BOGLE]

    r = await evaluate(service, out, into)  # no limit given: valid, with the hit reported
    assert (r.valid, r.hit, r.bank_after) == (True, 8, 1.5)
    assert (
        await evaluate(service, out, into, max_hit=8)
    ).valid  # the manager accepts an 8-point hit

    r = await evaluate(service, out, into, max_hit=4)  # only one hit's worth
    assert not r.valid
    assert has_violation(r, "8-point hit")

    r = await evaluate(service, out, into, max_hit=0)  # free transfers only
    assert not r.valid

    # One transfer is covered by the free one: no hit at all.
    assert (await evaluate(service, [GROSS], [BELLOUMI], max_hit=0)).valid


async def test_chips(service):
    out, into = [GROSS, WISSA, MUHAREMOVIC], [BELLOUMI, KOSTOULAS, BOGLE]

    # Team 6 still has its Free Hit: every transfer is free.
    r = await evaluate(service, out, into, chip="freehit")
    assert (r.valid, r.hit) == (True, 0)
    # Its first-half Wildcard is used up.
    assert has_violation(
        await evaluate(service, out, into, chip="wildcard"), "wildcard chip isn't available"
    )
    # Bench Boost doesn't make transfers free.
    assert has_violation(
        await evaluate(service, out, into, chip="bboost"), "only the wildcard or freehit"
    )


@pytest.mark.parametrize(
    ("out", "into", "expected_text"),
    [
        ([RAYA], [BELLOUMI], "1 goalkeepers"),  # a goalkeeper for a midfielder
        ([BELLOUMI], [KOSTOULAS], "not in the squad"),  # selling someone he doesn't own
        ([GROSS], [WISSA], "already in the squad"),  # buying someone he owns
        ([GROSS, WISSA], [BELLOUMI], "2 players out but 1 in"),
    ],
)
async def test_rule_violations(service, out, into, expected_text):
    r = await evaluate(service, out, into)
    assert not r.valid
    assert has_violation(r, expected_text)


async def test_requests_that_cannot_be_evaluated_are_errors(service):
    NOT_FOUND = grpc.StatusCode.NOT_FOUND
    err = await error_of(service.evaluate_transfers(pb.EvaluateTransfersRequest(team_id=6)))
    assert err.code == grpc.StatusCode.INVALID_ARGUMENT  # nothing to evaluate

    err = await error_of(evaluate(service, [GROSS], [99999]))
    assert err.code == NOT_FOUND

    request = pb.EvaluateTransfersRequest(
        team_id=999, player_out_ids=[GROSS], player_in_ids=[BELLOUMI]
    )
    assert (await error_of(service.evaluate_transfers(request))).code == NOT_FOUND


async def test_transfer_trends(service):
    # Direction left unset means "in".
    response = await service.get_transfer_trends(pb.GetTransferTrendsRequest())

    assert response.gameweek == 6
    assert len(response.players) == DEFAULT_LIMIT
    top = response.players[0]
    assert (top.player.web_name, top.player.transfers_in_event) == ("Groß", 763845)
    assert len(top.next_fixtures) == 3
    assert top.projection.horizon == 5  # so the agent can say whether the hype is justified

    out = await service.get_transfer_trends(
        pb.GetTransferTrendsRequest(direction=pb.TRANSFER_DIRECTION_OUT, limit=3)
    )
    counts = [o.player.transfers_out_event for o in out.players]
    assert len(counts) == 3
    assert counts == sorted(counts, reverse=True)


# ---- projections over several gameweeks ---------------------------------------------


async def test_evaluate_reports_projected_points_over_the_horizon(service):
    r = await evaluate(service, [GROSS], [BELLOUMI])

    assert r.horizon == 5  # the default
    # The projections are the sums of the players' own projections, which cover 5 gameweeks from the next (6).
    assert r.projected_points_out == pytest.approx(r.players_out[0].projection.total)
    assert r.projected_points_in == pytest.approx(r.players_in[0].projection.total)
    assert [g.gameweek for g in r.players_in[0].projection.gameweeks] == [6, 7, 8, 9, 10]
    # Both start, so the net is simply in - out - hit.
    assert r.projected_points_net == pytest.approx(
        r.projected_points_in - r.projected_points_out - r.hit, abs=0.02
    )
    assert r.projected_points_net < 0  # Groß (11.2 expected) is far better than Belloumi (7.0)


async def test_the_horizon_can_be_chosen_and_is_capped(service):
    assert (await evaluate(service, [GROSS], [BELLOUMI], horizon=2)).horizon == 2
    assert (await evaluate(service, [GROSS], [BELLOUMI], horizon=99)).horizon == 10


async def test_swapping_a_substitute_gains_nothing_even_if_the_new_player_scores(service):
    """Only 11 players score, so a better substitute adds nothing to the best eleven."""
    # Bentley (623) is team 6's backup goalkeeper and projects 0. Find a keeper who projects some
    # points, but fewer than the first choice (Raya, 1).
    found = await service.find_players(
        pb.FindPlayersRequest(position=pb.POSITION_GOALKEEPER, max_price=6.0, limit=20)
    )
    keeper = next(o for o in found.players if 1.0 < o.projection.total < 20.0)

    r = await evaluate(service, [623], [keeper.player.id], current_bank=4.0)

    assert r.projected_points_in > 1.0  # on paper, a gain of several points...
    assert (
        r.projected_points_net == 0
    )  # ...but he would not displace Raya, so the eleven is unchanged


async def test_a_blank_and_a_double_gameweek_show_in_the_projection(service):
    # Move Arsenal's gameweek 7 match to gameweek 8: blank in 7, double in 8.
    service.store.conn.execute("UPDATE fixtures SET gameweek = 8 WHERE id = 69")

    saka = (await evaluate(service, [GROSS], [SAKA])).players_in[0].projection

    assert [(g.gameweek, g.matches) for g in saka.gameweeks] == [
        (6, 1),
        (7, 0),
        (8, 2),
        (9, 1),
        (10, 1),
    ]
    assert saka.gameweeks[1].expected_points == 0
    assert (
        saka.gameweeks[2].expected_points > saka.gameweeks[3].expected_points * 1.5
    )  # about double


async def test_the_horizon_stops_at_the_end_of_the_season(service):
    service.store.conn.execute(
        "UPDATE events SET is_next = (id = 37)"
    )  # pretend gameweek 37 is next

    r = await evaluate(service, [GROSS], [BELLOUMI], horizon=5)

    assert r.horizon == 2  # gameweeks 37 and 38 only


async def test_after_the_season_there_is_nothing_to_project(service):
    service.store.conn.execute("UPDATE events SET is_next = 0")

    r = await evaluate(service, [GROSS], [BELLOUMI])

    assert (r.horizon, r.projected_points_in) == (0, 0)


# ---- the manager says how things stand now -------------------------------------------

# Team 6's squad is [1, 391, 8, 334, 31, 154, 565, 480, 40, 464, 411, 623, 124, 165, 115].


async def team_6_ids(service) -> list[int]:
    team = await service.get_manager_team(pb.GetManagerTeamRequest(team_id=6))
    return [p.player.id for p in team.players]


async def test_a_squad_given_by_the_manager_replaces_the_last_deadlines(service):
    # The manager has since swapped Groß (124) for Belloumi (286), and has £0.6m and no free transfer.
    ids = [BELLOUMI if i == GROSS else i for i in await team_6_ids(service)]

    r = await evaluate(
        service,
        [BELLOUMI],
        [GROSS],
        current_squad_ids=ids,
        current_bank=0.6,
        free_transfers_available=0,
    )

    assert not has_violation(r, "not in the squad")  # Belloumi counts as owned now
    assert not has_violation(r, "already in the squad")  # and Groß no longer does
    assert (r.free_transfers, r.hit, r.bank_before) == (0, 4, 0.6)
    assert any("current_squad_ids" in n for n in r.notes)
    # Belloumi was bought after the deadline: his selling price is his current £5.1m, an estimate.
    assert any("estimated selling price" in n for n in r.notes)
    assert r.bank_after == pytest.approx(
        0.6 + 5.1 - 5.8
    )  # sell Belloumi, buy Groß at today's price


async def test_the_bank_and_free_transfers_can_be_overridden_alone(service):
    r = await evaluate(
        service,
        [GROSS, WISSA, MUHAREMOVIC],
        [BELLOUMI, KOSTOULAS, BOGLE],
        current_bank=2.0,
        free_transfers_available=3,
    )

    assert (r.free_transfers, r.hit, r.bank_before) == (3, 0, 2.0)
    assert r.valid


async def test_a_new_squad_without_a_bank_warns_that_the_money_may_be_wrong(service):
    ids = await team_6_ids(service)
    r = await evaluate(service, [GROSS], [BELLOUMI], current_squad_ids=ids)

    assert any("current_bank was not given" in n for n in r.notes)
    assert any("free_transfers_available was not given" in n for n in r.notes)


@pytest.mark.parametrize(
    ("ids", "code"),
    [
        ([1, 2, 3], grpc.StatusCode.INVALID_ARGUMENT),  # not 15 players
        ([1] * 15, grpc.StatusCode.INVALID_ARGUMENT),  # duplicates
    ],
)
async def test_a_bad_squad_from_the_manager_is_rejected(service, ids, code):
    err = await error_of(service.evaluate_transfers(
        pb.EvaluateTransfersRequest(team_id=6, player_out_ids=[GROSS], player_in_ids=[BELLOUMI], current_squad_ids=ids)
    ))  # fmt: skip
    assert err.code == code


async def test_an_unknown_player_in_the_managers_squad_is_not_found(service):
    ids = (await team_6_ids(service))[:14] + [99999]
    err = await error_of(service.evaluate_transfers(
        pb.EvaluateTransfersRequest(team_id=6, player_out_ids=[GROSS], player_in_ids=[BELLOUMI], current_squad_ids=ids)
    ))  # fmt: skip
    assert err.code == grpc.StatusCode.NOT_FOUND


# ---- SuggestTransfers ------------------------------------------------------------------------


async def suggest(service, **fields) -> pb.SuggestTransfersResponse:
    return await service.suggest_transfers(pb.SuggestTransfersRequest(team_id=6, **fields))


async def test_suggestions_come_best_first_with_the_details_needed_to_explain_them(service):
    r = await suggest(service)

    assert r.horizon == 5
    assert len(r.suggestions) == 5
    gains = [s.expected_points_gain for s in r.suggestions]
    assert gains == sorted(gains, reverse=True)
    assert all(g > 0 for g in gains)

    best = r.suggestions[0]
    assert len(best.player_out_ids) == len(best.player_in_ids) == 1  # one free transfer, no hit
    assert best.hit == 0
    out, into = best.players_out[0], best.players_in[0]
    assert out.id == best.player_out_ids[0] and into.id == best.player_in_ids[0]
    assert out.web_name and into.web_name and out.team_name and into.team_name
    assert out.position == into.position  # a swap keeps the squad's shape
    assert into.projected_points > out.projected_points
    # The money adds up: bank + what the player sells for - what the new one costs.
    assert best.bank_after == pytest.approx(0.1 + out.price - into.price)


async def test_every_suggestion_is_valid_and_agrees_with_evaluate_transfers(service):
    """The two RPCs must tell the same story about the same transfers."""
    r = await suggest(service, max_transfers=3, current_bank=3.0, free_transfers_available=2)
    assert r.suggestions

    for s in r.suggestions:
        checked = await evaluate(
            service, s.player_out_ids, s.player_in_ids, current_bank=3.0, free_transfers_available=2
        )
        assert checked.valid, checked.violations
        assert checked.hit == s.hit
        assert checked.projected_points_net == pytest.approx(s.expected_points_gain, abs=0.011)
        assert checked.bank_after == pytest.approx(s.bank_after)


async def test_a_suggestion_never_buys_someone_already_owned_or_breaks_the_club_limit(service):
    owned = set(await team_6_ids(service))
    r = await suggest(service, max_transfers=3, current_bank=5.0, free_transfers_available=3)

    for s in r.suggestions:
        assert not owned & set(s.player_in_ids)


async def test_max_hit_zero_means_free_transfers_only(service):
    r = await suggest(service, max_transfers=3, max_hit=0)  # one free transfer
    assert r.suggestions
    assert all(s.hit == 0 and len(s.player_in_ids) == 1 for s in r.suggestions)


async def test_a_hit_is_suggested_only_when_it_pays_for_itself(service):
    with_hits = await suggest(service, max_transfers=3, current_bank=10.0, max_hit=8)
    free_only = await suggest(service, max_transfers=3, current_bank=10.0, max_hit=0)

    best = with_hits.suggestions[0]
    assert best.hit > 0  # with plenty of money, taking a hit beats staying with one free transfer
    assert (
        best.expected_points_gain > free_only.suggestions[0].expected_points_gain
    )  # after paying for it
    for s in with_hits.suggestions:
        assert s.hit == 4 * max(len(s.player_in_ids) - 1, 0)


async def test_at_most_three_transfers_are_considered(service):
    r = await suggest(service, max_transfers=9, current_bank=10.0, free_transfers_available=5)
    assert max(len(s.player_in_ids) for s in r.suggestions) == 3
    assert any("up to 3" in n for n in r.notes)


async def test_suggestions_respect_the_managers_current_squad(service):
    # The manager has already bought Belloumi (286) in place of Groß.
    ids = [BELLOUMI if i == GROSS else i for i in await team_6_ids(service)]

    r = await suggest(service, current_squad_ids=ids, current_bank=0.6, free_transfers_available=1)

    assert r.suggestions
    for s in r.suggestions:
        assert BELLOUMI not in s.player_in_ids  # already his
        assert GROSS not in s.player_out_ids  # no longer in his squad


async def test_when_nothing_is_worth_doing_the_answer_says_so(service):
    # No money at all and no free transfers: any move costs 4 points and the squad has nothing to sell for more.
    r = await suggest(service, max_transfers=1, max_hit=0, free_transfers_available=0)

    assert r.suggestions == []
    assert any("keeping the squad is best" in n for n in r.notes)


async def test_suggest_errors(service):
    err = await error_of(service.suggest_transfers(pb.SuggestTransfersRequest(team_id=999)))
    assert err.code == grpc.StatusCode.NOT_FOUND
    err = await error_of(
        service.suggest_transfers(pb.SuggestTransfersRequest(team_id=6, current_squad_ids=[1, 2]))
    )
    assert err.code == grpc.StatusCode.INVALID_ARGUMENT

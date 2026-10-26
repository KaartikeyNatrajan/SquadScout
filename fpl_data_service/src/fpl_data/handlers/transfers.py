"""GetTransferTrends, EvaluateTransfers and SuggestTransfers."""

import asyncio
from dataclasses import dataclass, field

from fpl_data import lineup, optimizer, squad
from fpl_data.db import gameweeks, players
from fpl_data.db.players import PlayerFilter, PlayerRow, PlayerSort
from fpl_data.handlers.base import MAX_SEARCH_RESULTS, ServiceBase, clamp_limit
from fpl_data.handlers.convert import pounds, round2
from fpl_data.handlers.errors import invalid_argument, not_found
from fpl_data.handlers.projector import DEFAULT_HORIZON, MAX_HORIZON, Projector
from fpl_data.manager.loader import Pick, Team
from squadscout.v1 import fpl_pb2 as pb

# Candidates fetched per position for the optimizer to choose from (it prunes them further).
CANDIDATES_PER_POSITION = 25


@dataclass
class CurrentSquad:
    """The squad a transfer is being planned for: what FPL showed at the last
    deadline, unless the manager said things have changed since."""

    picks: list[Pick]
    bank: int  # tenths of £m
    free_transfers: int
    notes: list[str] = field(default_factory=list)


class TransferHandlers(ServiceBase):
    async def get_transfer_trends(
        self, request: pb.GetTransferTrendsRequest
    ) -> pb.GetTransferTrendsResponse:
        """The players most transferred in (or out) by all FPL managers since
        the last deadline, with their form, news, fixtures and projection, so
        the reasons behind each move are in the same response."""
        transferred_in = request.direction != pb.TRANSFER_DIRECTION_OUT  # UNSPECIFIED means in
        rows = players.top_transfers(
            self.store, transferred_in, clamp_limit(request.limit, MAX_SEARCH_RESULTS)
        )

        # The counts are for transfers made towards the next gameweek.
        upcoming = gameweeks.next_gameweek(self.store)
        return pb.GetTransferTrendsResponse(
            gameweek=upcoming.id if upcoming else 0, players=self.outlooks(rows)
        )

    async def evaluate_transfers(
        self, request: pb.EvaluateTransfersRequest
    ) -> pb.EvaluateTransfersResponse:
        """Check a set of transfers for a manager's team against every FPL rule
        (via the squad module, the verifier) and report what they cost and what
        they're expected to gain.

        Rule breaches come back as plain-word violations with valid = false,
        not as errors: "you can't afford him" is a useful answer for the agent
        to reason about. Errors are kept for requests that can't be evaluated
        at all (an unknown team or player id).
        """
        team = await self.load_team(request.team_id)
        out_ids, in_ids = list(request.player_out_ids), list(request.player_in_ids)
        if not out_ids and not in_ids:
            raise invalid_argument("list at least one player out and one player in")

        current = self.current_squad(team, request)
        # Look up the squad and every player named in the request in one go.
        named = out_ids + in_ids
        found = players.players_by_id(self.store, named + [p.player_id for p in current.picks])
        for player_id in named:
            if player_id not in found:
                raise not_found(
                    f"no player with id {player_id}; use SearchPlayers or FindPlayers to find player ids"
                )

        # Run the transfers through the verifier. Players coming in cost their
        # current price; players in the squad are worth their selling price.
        incoming = [_member(found[i], found[i].now_cost) for i in in_ids]
        before = _squad_of(current, found)
        after, violations = squad.apply(before, out_ids, incoming)

        # Chips and the points hit.
        chip = request.chip.strip().lower()
        if chip and chip not in (squad.CHIP_WILDCARD, squad.CHIP_FREE_HIT):
            violations.append(
                f'only the wildcard or freehit chip makes transfers free, not "{chip}"'
            )
        elif chip and chip not in team.chips_available:
            violations.append(f"the {chip} chip isn't available for gameweek {team.next_gameweek}")
        hit = squad.hit_cost(len(in_ids), current.free_transfers, chip)
        if request.HasField("max_hit") and hit > request.max_hit:
            violations.append(
                f"{len(in_ids)} transfers with {current.free_transfers} free cost a {hit}-point hit, "
                f"more than the {request.max_hit} points allowed"
            )

        # Expected points: FPL's for the next gameweek, and ours over the horizon.
        projector = self.projector(_horizon(request.horizon))
        expected_out = sum(found[i].expected_points_next for i in out_ids)
        expected_in = sum(found[i].expected_points_next for i in in_ids)
        projected_out = sum(projector.project(found[i]).total for i in out_ids)
        projected_in = sum(projector.project(found[i]).total for i in in_ids)
        # What the transfers are really worth: the change in the best starting eleven.
        # (If the transfers made no sense, apply() returned the squad unchanged and
        # there is no "after" to compare, so fall back to the plain difference.)
        projected_net = projected_in - projected_out - hit
        if after is not before:
            points = {i: projector.project(p).total for i, p in found.items()}
            projected_net = (
                lineup.squad_points(after.members, points)
                - lineup.squad_points(before.members, points)
                - hit
            )

        notes = [
            "expected_points_* are FPL's projection for the next gameweek only. projected_points_* are "
            "our estimate over the horizon, built from recent minutes and points, availability and fixture "
            "difficulty; projected_points_net is the change in the projected points of your best starting "
            "eleven (so swapping a bench player gains nothing), minus the hit.",
            *current.notes,
        ]
        if any(p.selling_price_is_estimate and p.player_id in out_ids for p in current.picks):
            notes.append(
                "At least one player leaving has an estimated selling price, so bank_after may be slightly off."
            )

        return pb.EvaluateTransfersResponse(
            valid=not violations,
            violations=violations,
            transfers=len(in_ids),
            free_transfers=current.free_transfers,
            hit=hit,
            bank_before=pounds(current.bank),
            bank_after=pounds(_bank_after(current, found, out_ids, in_ids)),
            expected_points_out=round2(expected_out),
            expected_points_in=round2(expected_in),
            expected_points_net=round2(expected_in - expected_out - hit),
            players_out=self.outlooks([found[i] for i in out_ids], projector),
            players_in=self.outlooks([found[i] for i in in_ids], projector),
            notes=notes,
            horizon=projector.horizon,
            projected_points_out=round2(projected_out),
            projected_points_in=round2(projected_in),
            projected_points_net=round2(projected_net),
        )

    async def suggest_transfers(
        self, request: pb.SuggestTransfersRequest
    ) -> pb.SuggestTransfersResponse:
        """Search for the best transfers (1 to 3), ranked by projected points
        gained minus the points hit. Every suggestion has been checked against
        all the squad rules."""
        team = await self.load_team(request.team_id)
        current = self.current_squad(team, request)
        projector = self.projector(_horizon(request.horizon))

        squad_players = players.players_by_id(self.store, [p.player_id for p in current.picks])
        owned = [p.player_id for p in current.picks]

        # Candidates: the best available players per position by FPL's own expected
        # points, not already in the squad. The optimizer prunes them further.
        candidate_rows: dict[int, PlayerRow] = {}
        for position in (1, 2, 3, 4):
            flt = PlayerFilter(
                position=position,
                exclude_ids=owned,
                sort=PlayerSort.EXPECTED_POINTS,
                limit=CANDIDATES_PER_POSITION,
            )
            for row in players.find_players(self.store, flt):
                candidate_rows[row.id] = row

        free = current.free_transfers
        max_transfers = request.max_transfers if request.max_transfers > 0 else max(free, 1)
        max_transfers = min(max_transfers, optimizer.MAX_TRANSFERS)
        opt_request = optimizer.Request(
            squad=_squad_of(current, squad_players),
            free_transfers=free,
            max_transfers=max_transfers,
            max_hit=request.max_hit if request.HasField("max_hit") else None,
            expected_points={i: projector.project(p).total for i, p in squad_players.items()},
            candidates=[
                optimizer.Candidate(_member(row, row.now_cost), projector.project(row).total)
                for row in candidate_rows.values()
            ],
        )

        # The search is pure computation that can take a second or two. A worker
        # thread keeps the server answering other requests meanwhile (it touches no
        # database, so that is safe).
        suggestions = await asyncio.to_thread(optimizer.suggest, opt_request)

        sell_price = {p.player_id: p.selling_price for p in current.picks}
        notes = [
            "Suggestions maximise the projected points of your best starting eleven over the next "
            f"{projector.horizon} gameweeks, minus the points hit. Projections are rough estimates.",
            *current.notes,
        ]
        if request.max_transfers > optimizer.MAX_TRANSFERS:
            notes.append(f"Only up to {optimizer.MAX_TRANSFERS} transfers are considered.")
        if not suggestions:
            notes.append(
                "No legal transfer gains points within the hit allowed: keeping the squad is best."
            )

        return pb.SuggestTransfersResponse(
            suggestions=[
                self._suggestion_to_pb(
                    s, squad_players, candidate_rows, sell_price, current.bank, projector
                )
                for s in suggestions
            ],
            horizon=projector.horizon,
            notes=notes,
        )

    def current_squad(self, team: Team, request) -> CurrentSquad:
        """The squad to plan for. FPL shows a squad as it was at the last deadline,
        so transfers made since then are invisible. If the request says what the
        manager has now (`current_squad_ids`, `current_bank`,
        `free_transfers_available`), that wins; otherwise it is what FPL showed."""
        current = CurrentSquad(list(team.picks), team.bank, team.free_transfers)

        ids = list(request.current_squad_ids)
        if ids:
            if len(ids) != squad.SIZE or len(set(ids)) != squad.SIZE:
                raise invalid_argument(
                    f"current_squad_ids must list all {squad.SIZE} different players in the squad"
                )
            known = {p.player_id: p for p in team.picks}
            current.picks = []
            for i, player_id in enumerate(ids, start=1):
                if player_id in known:
                    current.picks.append(known[player_id])
                    continue
                player = players.get_player(self.store, player_id)
                if player is None:
                    raise not_found(
                        f"no player with id {player_id}; use SearchPlayers or FindPlayers to find player ids"
                    )
                # Bought since the deadline: we don't know what for, so use today's price.
                current.picks.append(
                    Pick(
                        player_id=player_id,
                        squad_position=i,
                        is_captain=False,
                        is_vice_captain=False,
                        selling_price=player.now_cost,
                        selling_price_is_estimate=True,
                    )
                )
            current.notes.append(
                "Using the squad given in current_squad_ids instead of the last deadline's."
            )
            if not request.HasField("current_bank"):
                current.notes.append(
                    "current_bank was not given, so the bank is as at the last deadline and may be wrong."
                )

        if request.HasField("current_bank"):
            current.bank = round(request.current_bank * 10)  # £m to tenths
        if request.HasField("free_transfers_available"):
            current.free_transfers = max(request.free_transfers_available, 0)
        elif ids:
            current.notes.append(
                "free_transfers_available was not given, so the free transfers are as at the last "
                "deadline and may be too many."
            )
        return current

    @staticmethod
    def _suggestion_to_pb(
        s: optimizer.Suggestion,
        squad_players: dict[int, PlayerRow],
        candidates: dict[int, PlayerRow],
        sell_price: dict[int, int],
        bank: int,
        projector: Projector,
    ) -> pb.TransferSuggestion:
        def entry(row: PlayerRow, price: int) -> pb.TransferPlayer:
            return pb.TransferPlayer(
                id=row.id,
                web_name=row.web_name,
                team_name=row.team_name,
                position=row.position,
                price=pounds(price),
                projected_points=round2(projector.project(row).total),
            )

        players_out = [entry(squad_players[i], sell_price[i]) for i in s.out_ids]
        players_in = [entry(candidates[i], candidates[i].now_cost) for i in s.in_ids]
        bank_after = (
            bank
            + sum(sell_price[i] for i in s.out_ids)
            - sum(candidates[i].now_cost for i in s.in_ids)
        )
        return pb.TransferSuggestion(
            player_out_ids=s.out_ids,
            player_in_ids=s.in_ids,
            hit=s.hit,
            expected_points_gain=round2(s.expected_gain),
            players_out=players_out,
            players_in=players_in,
            bank_after=pounds(bank_after),
        )


def _horizon(requested: int) -> int:
    """The number of gameweeks to project over: 0 means the default."""
    if requested <= 0:
        return DEFAULT_HORIZON
    return min(requested, MAX_HORIZON)


def _member(player: PlayerRow, price: int) -> squad.Member:
    """A player as a squad.Member worth `price` (tenths of £m)."""
    return squad.Member(
        player_id=player.id,
        name=player.web_name,
        position=player.position,
        club_id=player.team_id,
        club_name=player.team_name,
        price=price,
    )


def _squad_of(current: CurrentSquad, found: dict[int, PlayerRow]) -> squad.Squad:
    """The squad in the squad module's form, with each player valued at his selling price."""
    return squad.Squad(
        members=[_member(found[p.player_id], p.selling_price) for p in current.picks],
        bank=current.bank,
    )


def _bank_after(
    current: CurrentSquad, found: dict[int, PlayerRow], out_ids: list[int], in_ids: list[int]
) -> int:
    """The money left after the transfers: the bank, plus the selling price of
    each player leaving, minus the price of each joining. It's worked out even
    when the transfers break a rule, so the agent can see how far over budget a
    plan is."""
    bank = current.bank
    for pick in current.picks:
        if pick.player_id in out_ids:
            bank += pick.selling_price
    for player_id in in_ids:
        bank -= found[player_id].now_cost
    return bank

"""Loading a manager's team.

A team is downloaded the first time it's asked for and then saved in SQLite
until the next deadline. That's safe because FPL hides a squad's new picks
until the deadline passes, so nothing we can see changes before then (except
overall points and rank, which can wait). In practice a manager's first
question in a gameweek costs 4-5 FPL requests, and every later one, even after
a restart, costs none.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from fpl_data import squad
from fpl_data.db import gameweeks, manager_teams, player_prices, players
from fpl_data.db.store import Store
from fpl_data.fpl.client import FplClient, NotFoundError
from fpl_data.fpl.models import ChipPlay, EntryTransfer
from fpl_data.fpl.models import Pick as FplPick
from fpl_data.manager import rules

logger = logging.getLogger(__name__)

# How long a team is kept when its next deadline is unknown or has just passed
# (FPL takes a few minutes to publish the new squads).
RECHECK_AFTER = timedelta(minutes=10)


class ManagerError(Exception):
    """Base class for the problems callers may want to tell apart."""


class TeamNotFoundError(ManagerError):
    """No FPL team with that id."""


class NoSquadYetError(ManagerError):
    """The team hasn't played a gameweek yet, so its squad isn't public."""


class NotReadyError(ManagerError):
    """The squad includes a player we haven't downloaded yet (FPL just added him)."""


class Pick(BaseModel):
    """One player in the squad."""

    player_id: int
    squad_position: int  # 1-11 starting XI, 12-15 bench
    is_captain: bool
    is_vice_captain: bool
    selling_price: int  # tenths of £m
    # True when we don't know what he was bought for, so selling_price is just
    # his current price.
    selling_price_is_estimate: bool


class Team(BaseModel):
    """A manager's team as of the last deadline, ready for the next gameweek.
    Money is in tenths of £m. (A pydantic model so it converts to JSON and back
    for saving.)"""

    id: int
    name: str
    manager_name: str
    overall_points: int
    overall_rank: int  # 0 before the first gameweek is scored

    squad_gameweek: int  # the gameweek whose squad `picks` is
    next_gameweek: int  # the gameweek the squad is being prepared for
    bank: int
    free_transfers: int  # an estimate, see rules.free_transfers
    chips_available: list[str]
    picks: list[Pick]  # in squad position order
    notes: list[str]  # caveats about the numbers above, in plain words


class Loader:
    """Downloads teams and saves each in the store until its next deadline."""

    def __init__(self, client: FplClient, store: Store, now: Callable[[], datetime] | None = None):
        self.client = client
        self.store = store
        # The clock. It's a parameter so tests can move time past a deadline;
        # everywhere else it's the real time.
        self.now = now or (lambda: datetime.now(UTC))

    async def load(self, team_id: int) -> Team:
        """The team with this id: the saved copy if its deadline hasn't passed,
        otherwise a fresh download (which is then saved).

        If the download fails because FPL is down, an out-of-date saved copy is
        returned with a note saying so: an old squad beats no squad.
        """
        saved = manager_teams.load_manager_team(self.store, team_id)
        if saved is not None and self.now() < saved.valid_until:
            return Team.model_validate_json(saved.data)

        try:
            team = await self._download(team_id)
        except (TeamNotFoundError, NoSquadYetError):
            raise  # "no such team" is an answer, not an outage: don't paper over it
        except Exception as err:
            if saved is None:
                raise
            logger.warning(
                "team download failed, serving saved copy: team_id=%d err=%s", team_id, err
            )
            stale = Team.model_validate_json(saved.data)
            stale.notes.append(
                f"FPL couldn't be reached, so this is the copy saved at "
                f"{saved.fetched_at.strftime('%Y-%m-%dT%H:%M:%SZ')} and may be out of date."
            )
            return stale

        logger.info("team downloaded: team_id=%d squad_gameweek=%d", team_id, team.squad_gameweek)
        self._save(team)
        return team

    def _save(self, team: Team) -> None:
        """Store `team` until its next deadline."""
        manager_teams.save_manager_team(
            self.store,
            team.id,
            team.model_dump_json(),
            self.now(),
            self._valid_until(team.next_gameweek),
        )

    def _valid_until(self, next_gameweek: int) -> datetime:
        """When a team downloaded now may next change: the deadline of the
        gameweek it's being prepared for. If that deadline is unknown (season
        over) or already past (FPL is still publishing), check again shortly."""
        now = self.now()
        deadline = gameweeks.deadline(self.store, next_gameweek)
        if deadline is None:
            return now + RECHECK_AFTER
        moment = datetime.fromisoformat(deadline)
        return moment if moment > now else now + RECHECK_AFTER

    async def _download(self, team_id: int) -> Team:
        """Fetch everything about a team from the FPL API and work out the
        numbers the API doesn't give directly."""
        try:
            entry = await self.client.fetch_entry(team_id)
        except NotFoundError as err:
            raise TeamNotFoundError(f"team {team_id}: no FPL team with that id") from err
        if entry.current_event is None:
            raise NoSquadYetError(
                f"team {team_id}: this team hasn't played a gameweek yet, so its squad isn't public"
            )
        last_deadline = entry.current_event

        team = Team(
            id=entry.id,
            name=entry.name,
            manager_name=f"{entry.player_first_name} {entry.player_last_name}",
            overall_points=entry.summary_overall_points,
            overall_rank=entry.summary_overall_rank or 0,
            squad_gameweek=last_deadline,
            next_gameweek=last_deadline + 1,
            bank=0,
            free_transfers=0,
            chips_available=[],
            picks=[],
            notes=[
                f"FPL only shows a squad after the deadline, so this is the squad at the gameweek {last_deadline} "
                "deadline; transfers made since then are not visible.",
                "free_transfers is an estimate worked out from the transfer history.",
                f"Overall points and rank are as of {self.now().astimezone(UTC):%-d %b %H:%M} UTC; "
                "they update at the next deadline.",
            ],
        )

        # These three don't depend on each other, so ask for them all at once:
        # a first request is then as slow as the slowest one, not the sum of three.
        picks, history, transfers = await asyncio.gather(
            self.client.fetch_entry_picks(team_id, last_deadline),
            self.client.fetch_entry_history(team_id),
            self.client.fetch_entry_transfers(team_id),
        )
        # A Free Hit squad lasts one gameweek; afterwards the team goes back to
        # the squad it had before. That earlier squad is the one to plan with.
        if picks.active_chip == squad.CHIP_FREE_HIT and last_deadline > entry.started_event:
            team.squad_gameweek = last_deadline - 1
            team.notes.append(
                f"A Free Hit was played in gameweek {last_deadline}, so the squad shown is the one from "
                f"gameweek {team.squad_gameweek}, which the team returns to."
            )
            picks = await self.client.fetch_entry_picks(team_id, team.squad_gameweek)

        # The bank as it was after the squad's gameweek (after a Free Hit, the
        # money also goes back to what it was before).
        for week in history.current:
            if week.event == team.squad_gameweek:
                team.bank = week.bank
        team.free_transfers = rules.free_transfers(
            entry.started_event, last_deadline, history.current, history.chips
        )
        team.chips_available = rules.chips_available(
            gameweeks.chips(self.store), history.chips, team.next_gameweek
        )

        await self._add_picks(team, picks.picks, transfers, history.chips, entry.started_event)
        return team

    async def _add_picks(
        self,
        team: Team,
        picks: list[FplPick],
        transfers: list[EntryTransfer],
        chips: list[ChipPlay],
        started_event: int,
    ) -> None:
        """Fill team.picks, working out each player's selling price from his
        current price (from the store) and what the team paid for him."""
        current = players.players_by_id(self.store, [p.element for p in picks])
        free_hit_events = {c.event for c in chips if c.name == squad.CHIP_FREE_HIT}
        paid = rules.purchase_prices(transfers, free_hit_events)

        # Players who were never bought through a transfer have been in the
        # squad since it was created. The API doesn't say what they cost, but
        # it's their price when the team started.
        original_ids = [p.element for p in picks if p.element in current and p.element not in paid]
        await self._download_price_histories(original_ids)

        estimated_ids: list[int] = []
        from_history = False
        for p in picks:
            player = current.get(p.element)
            if player is None:
                # Only happens if FPL added a player since our last refresh.
                raise NotReadyError(
                    f"player {p.element} in team {team.id} is not in the cache yet; try again after the next refresh"
                )
            purchase = paid.get(p.element)
            if purchase is None:
                purchase = player_prices.price_at(self.store, p.element, started_event)
                from_history = from_history or purchase is not None
            if purchase is None:
                price, estimate = player.now_cost, True  # no way to know: assume it hasn't changed
                estimated_ids.append(p.element)
            else:
                price, estimate = rules.selling_price(purchase, player.now_cost), False
            team.picks.append(
                Pick(
                    player_id=p.element,
                    squad_position=p.position,
                    is_captain=p.is_captain,
                    is_vice_captain=p.is_vice_captain,
                    selling_price=price,
                    selling_price_is_estimate=estimate,
                )
            )

        if from_history:
            team.notes.append(
                f"Players held since the squad was created are priced as they cost in gameweek {started_event}; "
                "that is exact unless prices moved between creating the team and that deadline."
            )
        if estimated_ids:
            team.notes.append(
                "Some players' purchase price could not be found, so their current price is used as their "
                "selling price."
            )

    async def _download_price_histories(self, player_ids: list[int]) -> None:
        """Make sure each of these players' price history is stored, downloading
        the missing ones together. A player whose download fails is skipped (his
        selling price then falls back to an estimate): that is no reason to
        fail the whole team."""
        missing = [i for i in player_ids if not player_prices.has_prices(self.store, i)]
        results = await asyncio.gather(
            *(self.client.fetch_element_summary(i) for i in missing), return_exceptions=True
        )
        for player_id, result in zip(missing, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning("price history of player %d unavailable: %s", player_id, result)
                continue
            player_prices.save_prices(
                self.store, player_id, [(h.round, h.value) for h in result.history]
            )

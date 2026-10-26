"""FPL's squad rules, and a checker for them: the "verifier".

Given a squad and a set of transfers, it works out the squad that results and
lists every rule it breaks, in plain words.

It only does arithmetic on the values it's given: no database, no network.
That keeps it easy to test, and fast enough for the optimizer to check
thousands of candidate transfer sets.
"""

from dataclasses import dataclass, field

# FPL's squad rules (they're also in /bootstrap-static/ "game_settings").
SIZE = 15  # players in a squad
MAX_PER_CLUB = 3  # players from any one Premier League club
HIT_PER_TRANSFER = 4  # points deducted per transfer beyond the free ones
MAX_FREE_TRANSFERS = 5  # free transfers can be saved up to this many

# How many players of each position a squad must have, keyed by FPL's position
# number (1 GK, 2 DEF, 3 MID, 4 FWD).
QUOTA = {1: 2, 2: 5, 3: 5, 4: 3}
_POSITION_NAMES = {1: "goalkeepers", 2: "defenders", 3: "midfielders", 4: "forwards"}

# Chips that make every transfer in their gameweek free.
CHIP_WILDCARD = "wildcard"
CHIP_FREE_HIT = "freehit"


@dataclass(frozen=True)
class Member:
    """One player, with just what the rules need."""

    player_id: int
    name: str  # only used in messages
    position: int  # 1 GK .. 4 FWD
    club_id: int
    club_name: str  # only used in messages
    # Tenths of £m: what he'd sell for if he's in the squad, or what he costs
    # to buy if he's coming in.
    price: int


@dataclass
class Squad:
    """A set of players plus the money left over."""

    members: list[Member] = field(default_factory=list)
    bank: int = 0  # tenths of £m; negative means over budget


def validate(squad: Squad) -> list[str]:
    """Every rule `squad` breaks. An empty list means it's a legal squad."""
    problems: list[str] = []

    if len(squad.members) != SIZE:
        problems.append(f"the squad has {len(squad.members)} players; it must have exactly {SIZE}")

    # Count players per position and per club in one pass.
    per_position: dict[int, int] = {}
    per_club: dict[int, int] = {}
    club_names: dict[int, str] = {}
    seen: set[int] = set()
    for m in squad.members:
        if m.player_id in seen:
            problems.append(f"{m.name} is in the squad twice")
        seen.add(m.player_id)
        per_position[m.position] = per_position.get(m.position, 0) + 1
        per_club[m.club_id] = per_club.get(m.club_id, 0) + 1
        club_names[m.club_id] = m.club_name

    # Positions in a fixed order (GK, DEF, MID, FWD) so messages are stable.
    for position in (1, 2, 3, 4):
        got, want = per_position.get(position, 0), QUOTA[position]
        if got != want:
            problems.append(
                f"the squad would have {got} {_POSITION_NAMES[position]}; it must have exactly {want}"
            )

    for club_id in sorted(per_club):
        if per_club[club_id] > MAX_PER_CLUB:
            problems.append(
                f"the squad would have {per_club[club_id]} {club_names[club_id]} players; the limit is {MAX_PER_CLUB}"
            )

    if squad.bank < 0:
        problems.append(f"the squad costs {_money(-squad.bank)} more than the budget allows")
    return problems


def apply(squad: Squad, out_ids: list[int], incoming: list[Member]) -> tuple[Squad, list[str]]:
    """Sell the players in `out_ids`, buy `incoming`, and return the resulting
    squad with every problem found: transfers that make no sense (selling
    someone not in the squad, buying someone already in it) and every rule the
    resulting squad breaks.

    If the transfers themselves make no sense, the original squad is returned
    unchanged with those problems; checking the rules on top would only add noise.
    """
    problems: list[str] = []
    if len(out_ids) != len(incoming):
        problems.append(
            f"{len(out_ids)} players out but {len(incoming)} in; the numbers must match"
        )

    # Index the squad by player id for quick lookups.
    current = {m.player_id: m for m in squad.members}

    leaving: set[int] = set()
    for player_id in out_ids:
        if player_id not in current:
            problems.append(f"player {player_id} is not in the squad, so he can't be sold")
        elif player_id in leaving:
            problems.append(f"{current[player_id].name} is sold twice")
        leaving.add(player_id)

    joining: set[int] = set()
    for m in incoming:
        if m.player_id in leaving:
            problems.append(f"{m.name} is both sold and bought")
        elif m.player_id in current:
            problems.append(f"{m.name} is already in the squad")
        elif m.player_id in joining:
            problems.append(f"{m.name} is bought twice")
        joining.add(m.player_id)

    if problems:
        return squad, problems

    # Build the new squad: everyone not leaving, plus the new players.
    # Selling adds each player's selling price to the bank; buying subtracts
    # the new player's price.
    new_squad = Squad(bank=squad.bank)
    for m in squad.members:
        if m.player_id in leaving:
            new_squad.bank += m.price
        else:
            new_squad.members.append(m)
    for m in incoming:
        new_squad.bank -= m.price
        new_squad.members.append(m)
    return new_squad, validate(new_squad)


def hit_cost(transfers: int, free_transfers: int, chip: str = "") -> int:
    """The points deducted for making `transfers` when `free_transfers` are
    available. Playing a Wildcard or Free Hit (`chip`) makes them all free."""
    if chip in (CHIP_WILDCARD, CHIP_FREE_HIT):
        return 0
    return max(transfers - free_transfers, 0) * HIT_PER_TRANSFER


def _money(tenths: int) -> str:
    """Format tenths of £m as "£1.5m"."""
    return f"£{tenths / 10:.1f}m"

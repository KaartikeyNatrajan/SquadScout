"""Loading the saved FPL responses in tests/testdata."""

import json
from typing import Any

from fpl_data.fpl import models
from tests.fake_fpl import TESTDATA


def load_json(name: str) -> Any:
    return json.loads((TESTDATA / name).read_text(encoding="utf-8"))


def load_bootstrap() -> models.Bootstrap:
    return models.Bootstrap.model_validate(load_json("bootstrap-static.json"))


def load_fixtures() -> list[models.Fixture]:
    return [models.Fixture.model_validate(f) for f in load_json("fixtures.json")]


def load_gameweek(gameweek: int) -> models.EventLive:
    """Every player's saved stats for one gameweek (1-5)."""
    return models.EventLive.model_validate(load_json(f"event-{gameweek}-live.json"))


def count_rows(store, table: str) -> int:
    # Table names can't be ? parameters; this is safe only because the tests
    # pass fixed names.
    return store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


async def error_of(awaitable):
    """Await a handler call that must fail, and return its ServiceError."""
    from fpl_data.handlers.errors import ServiceError

    try:
        await awaitable
    except ServiceError as err:
        return err
    raise AssertionError("expected a ServiceError, but the call succeeded")


def valid_squad(bank: int = 10):
    """A legal squad of made-up players: 2 GK, 5 DEF, 5 MID, 3 FWD, spread over
    clubs 1-8 so no club has more than 2, every player worth £5.0m, and £1.0m
    in the bank by default. Player ids are 1-15 (1-2 GK, 3-7 DEF, 8-12 MID, 13-15 FWD)."""
    from fpl_data.squad import Member, Squad

    positions = [1, 1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 4, 4, 4]
    return Squad(
        members=[
            Member(
                player_id=i + 1,
                name=f"P{chr(ord('A') + i)}",  # PA, PB, ...
                position=position,
                club_id=i % 8 + 1,
                club_name=f"Club{i % 8 + 1}",
                price=50,
            )
            for i, position in enumerate(positions)
        ],
        bank=bank,
    )

"""Reading past seasons from the archive of FPL data at
github.com/vaastav/Fantasy-Premier-League (one folder per season).

Per season we read three small CSV files:

    teams.csv        that season's team ids -> clubs (with the club's lasting `code`)
    players_raw.csv  that season's player ids -> players (with the lasting `code`)
    fixtures.csv     every match, its score, and FPL's per-match `stats`

The archive is plain files on GitHub, not the FPL API, so its rate limits don't apply.
"""

import ast
import csv
import io

import httpx

from fpl_data.fpl.models import Fixture
from fpl_data.history.results import Club, Person, Result, to_results

DEFAULT_BASE_URL = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
TIMEOUT_S = 30.0


class ArchiveError(Exception):
    """A season couldn't be downloaded or read."""


class ArchiveClient:
    def __init__(
        self, base_url: str = DEFAULT_BASE_URL, transport: httpx.AsyncBaseTransport | None = None
    ):
        # `transport` lets tests serve saved files instead of the internet.
        self._http = httpx.AsyncClient(base_url=base_url, timeout=TIMEOUT_S, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def fetch_season(self, season: str) -> list[Result]:
        """Every finished match of `season` ("2024-25") with its events."""
        teams = await self._csv(f"/{season}/teams.csv")
        players = await self._csv(f"/{season}/players_raw.csv")
        fixtures = await self._csv(f"/{season}/fixtures.csv")
        try:
            clubs = {int(t["id"]): Club(int(t["code"]), t["name"]) for t in teams}
            people = {int(p["id"]): Person(int(p["code"]), p["web_name"]) for p in players}
            return to_results([_fixture(row) for row in fixtures], clubs, people)
        except (KeyError, ValueError, SyntaxError) as err:
            raise ArchiveError(f"{season}: unexpected data: {err!r}") from err

    async def _csv(self, path: str) -> list[dict[str, str]]:
        try:
            response = await self._http.get(path)
            response.raise_for_status()
        except httpx.HTTPError as err:
            raise ArchiveError(f"{path}: {err}") from err
        return list(csv.DictReader(io.StringIO(response.text)))


def _fixture(row: dict[str, str]) -> Fixture:
    """A fixtures.csv row as the FPL model. The CSV writes missing values as "" and
    the stats column as a Python literal (single quotes), not JSON."""
    values = {key: (None if value == "" else value) for key, value in row.items()}
    values["stats"] = ast.literal_eval(row["stats"]) if row.get("stats") else []
    return Fixture.model_validate(values)

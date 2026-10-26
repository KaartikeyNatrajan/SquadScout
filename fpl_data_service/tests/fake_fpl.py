"""A fake FPL API for tests.

It serves the saved real responses in tests/testdata, so tests in any module
can exercise the full download -> store -> query path without the internet.
Pass `fake.transport` to FplClient; nothing touches the network.

    /bootstrap-static/              bootstrap-static.json
    /fixtures/                      fixtures.json
    /event/{gw}/live/               event-{gw}-live.json
    /element-summary/{id}/          element-summary-{id}.json
    /entry/{id}/                    entry-{id}.json
    /entry/{id}/history/            entry-{id}-history.json
    /entry/{id}/transfers/          entry-{id}-transfers.json
    /entry/{id}/event/{gw}/picks/   entry-{id}-picks-{gw}.json

and 404 (like the real API for an unknown id) when there's no such file.
It counts requests per path (`hits`) and can pretend to be down (`down`).
"""

from collections import Counter
from pathlib import Path

import httpx

TESTDATA = Path(__file__).parent / "testdata"


def testdata_file(path: str) -> str | None:
    """Map an API path to the name of the file with its saved response."""
    # "/entry/1/event/5/picks/" -> ["entry", "1", "event", "5", "picks"]
    parts = path.strip("/").split("/")
    match parts:
        case ["bootstrap-static"]:
            return "bootstrap-static.json"
        case ["fixtures"]:
            return "fixtures.json"
        case ["event", gameweek, "live"]:
            return f"event-{gameweek}-live.json"
        case ["element-summary", player_id]:
            return f"element-summary-{player_id}.json"
        case ["entry", team_id]:
            return f"entry-{team_id}.json"
        case ["entry", team_id, ("history" | "transfers") as what]:
            return f"entry-{team_id}-{what}.json"
        case ["entry", team_id, "event", gameweek, "picks"]:
            return f"entry-{team_id}-picks-{gameweek}.json"
    return None


class FakeFpl:
    def __init__(self) -> None:
        self.down = False  # True: every request fails with 503
        # Answer the next requests with 429 ("too many requests") before behaving
        # normally again; `retry_after` goes in the Retry-After header (None: no header).
        self.rate_limit_next = 0
        self.retry_after: str | None = "2"
        self._hits: Counter[str] = Counter()
        self.transport = httpx.MockTransport(self._handle)

    def hits(self, path: str) -> int:
        """How many requests arrived for `path`, e.g. "/fixtures/"."""
        return self._hits[path]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self._hits[path] += 1

        if self.rate_limit_next > 0:
            self.rate_limit_next -= 1
            headers = {"retry-after": self.retry_after} if self.retry_after is not None else {}
            return httpx.Response(429, text="slow down", headers=headers)
        if self.down:
            return httpx.Response(503, text="fake FPL API is down")
        name = testdata_file(path)
        file = TESTDATA / name if name else None
        if file is None or not file.exists():
            return httpx.Response(404, text="not found")  # like the real API for an unknown id
        return httpx.Response(
            200, content=file.read_bytes(), headers={"content-type": "application/json"}
        )

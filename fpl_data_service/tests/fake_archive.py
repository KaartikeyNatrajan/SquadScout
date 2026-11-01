"""A fake of the archive of past seasons (see fpl_data/history/archive.py).

Serves tests/testdata/archive/{season}/{file}: a real slice of 2024-25 and
2025-26 (Arsenal's matches against Liverpool, Ipswich and Sunderland), and
404 for anything else, like other seasons. Counts requests in `hits`.
"""

from collections import Counter

import httpx

from tests.fake_fpl import TESTDATA

ARCHIVE = TESTDATA / "archive"


class FakeArchive:
    def __init__(self) -> None:
        self.hits: Counter[str] = Counter()
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.lstrip("/")
        self.hits[path] += 1
        file = ARCHIVE / path
        if not file.is_file():
            return httpx.Response(404, text="404: Not Found")
        return httpx.Response(200, text=file.read_text(encoding="utf-8"))

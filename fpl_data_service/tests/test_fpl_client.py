import httpx
import pytest

from fpl_data.fpl.client import (
    MAX_CONCURRENT_REQUESTS,
    FplClient,
    FplError,
    NotFoundError,
    RateLimitedError,
)
from tests.helpers import load_bootstrap


def test_saved_bootstrap_decodes():
    b = load_bootstrap()
    assert (len(b.teams), len(b.elements), len(b.events)) == (20, 667, 38)


async def test_fetch_bootstrap(client):
    b = await client.fetch_bootstrap()
    assert len(b.elements) == 667
    # Two of each of the 4 chips: one per half of the season.
    assert len(b.chips) == 8
    saka = next(p for p in b.elements if p.id == 12)
    assert (saka.transfers_in_event, saka.transfers_out_event) == (99910, 44545)
    # FPL's decimals arrive as strings; the model converts them. Null stays None.
    assert saka.expected_goals == 3.22
    assert saka.chance_of_playing_next_round is None


async def test_fetch_fixtures(client):
    fixtures = await client.fetch_fixtures()
    assert len(fixtures) == 380

    # Fixture 1 was played: Arsenal (1) 3-0 Coventry (7).
    first = fixtures[0]
    assert (first.id, first.finished, first.team_h, first.team_a) == (1, True, 1, 7)
    assert first.team_h_score == 3

    # An unplayed fixture must decode its null scores as None, not 0.
    unplayed = next(f for f in fixtures if not f.finished)
    assert unplayed.team_h_score is None
    assert unplayed.team_a_score is None


async def test_fetch_event_live(client):
    live = await client.fetch_event_live(1)
    # Every player registered by gameweek 1, not just the ones who played.
    assert len(live.elements) == 610

    # Saka (12) in gameweek 1: 67 minutes, 1 goal, 9 points, 0.64 xG in fixture 1.
    saka = next(p for p in live.elements if p.id == 12)
    assert (saka.stats.minutes, saka.stats.goals_scored, saka.stats.total_points) == (67, 1, 9)
    assert saka.stats.expected_goals == 0.64
    assert len(saka.explain) == 1
    assert saka.explain[0].fixture == 1
    # The per-match breakdown adds up to the gameweek totals.
    assert (saka.explain[0].minutes(), saka.explain[0].points()) == (67, 9)


async def test_fetch_entry(client):
    e = await client.fetch_entry(6)
    assert (e.id, e.started_event, e.current_event) == (6, 1, 5)


async def test_fetch_entry_history(client):
    h = await client.fetch_entry_history(6)
    assert (len(h.current), len(h.chips)) == (5, 3)
    gw5 = h.current[4]
    # Two transfers in gameweek 5, £0.1m left in the bank.
    assert (gw5.event, gw5.event_transfers, gw5.bank) == (5, 2, 1)


async def test_fetch_entry_picks(client):
    p = await client.fetch_entry_picks(1, 5)
    assert len(p.picks) == 15
    assert p.active_chip == "freehit"  # team 1 played a Free Hit in gameweek 5


async def test_fetch_entry_transfers(client):
    transfers = await client.fetch_entry_transfers(6)
    assert len(transfers) == 21
    assert all(t.element_in and t.element_in_cost and t.event for t in transfers)


async def test_unknown_things_raise_not_found(client):
    with pytest.raises(NotFoundError):
        await client.fetch_entry(999)
    # A gameweek with no saved picks stands in for one whose deadline hasn't passed.
    with pytest.raises(NotFoundError):
        await client.fetch_entry_picks(1, 6)
    with pytest.raises(NotFoundError):
        await client.fetch_event_live(39)


async def test_api_down_raises_fpl_error(client, fake_fpl):
    fake_fpl.down = True
    with pytest.raises(FplError, match="503"):
        await client.fetch_bootstrap()


async def test_bad_json_raises_fpl_error():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text='{"elements": 5}'))
    client = FplClient("http://fpl.test", transport=transport)
    with pytest.raises(FplError, match="decode"):
        await client.fetch_bootstrap()
    await client.aclose()


async def test_sends_browser_like_user_agent():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["user-agent"] = request.headers["user-agent"]
        return httpx.Response(200, json=[])

    client = FplClient("http://fpl.test", transport=httpx.MockTransport(handler))
    await client.fetch_fixtures()
    await client.aclose()
    assert seen["user-agent"].startswith("Mozilla/5.0")


@pytest.fixture
def napping_client(fake_fpl):
    """A client whose sleeps are recorded instead of waited for."""
    naps: list[float] = []

    async def sleep(seconds: float) -> None:
        naps.append(seconds)

    client = FplClient("http://fpl.test", transport=fake_fpl.transport, sleep=sleep)
    return client, naps


async def test_a_429_is_retried_after_the_time_the_api_asks(napping_client, fake_fpl):
    client, naps = napping_client
    fake_fpl.rate_limit_next = 2  # two 429s in a row, then it works
    fake_fpl.retry_after = "3"

    fixtures = await client.fetch_fixtures()

    assert len(fixtures) == 380
    assert naps == [3.0, 3.0]
    assert fake_fpl.hits("/fixtures/") == 3
    await client.aclose()


async def test_without_a_retry_after_header_the_wait_grows(napping_client, fake_fpl):
    client, naps = napping_client
    fake_fpl.rate_limit_next = 2
    fake_fpl.retry_after = None

    await client.fetch_fixtures()

    assert naps == [1.0, 2.0]
    await client.aclose()


async def test_a_long_retry_after_is_reported_instead_of_waited_for(napping_client, fake_fpl):
    client, naps = napping_client
    fake_fpl.rate_limit_next = 1
    fake_fpl.retry_after = "300"

    with pytest.raises(RateLimitedError) as caught:
        await client.fetch_fixtures()

    assert caught.value.retry_after == 300
    assert naps == []  # nobody was kept waiting
    assert fake_fpl.hits("/fixtures/") == 1
    await client.aclose()


async def test_giving_up_after_too_many_429s(napping_client, fake_fpl):
    client, naps = napping_client
    fake_fpl.rate_limit_next = 99

    with pytest.raises(RateLimitedError):
        await client.fetch_fixtures()

    assert len(naps) == 2  # MAX_RETRIES waits, then it stops
    await client.aclose()


async def test_requests_in_flight_are_limited():
    import asyncio

    in_flight = peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)  # stay "in flight" long enough for the others to pile up
        in_flight -= 1
        return httpx.Response(200, json=[])

    client = FplClient("http://fpl.test", transport=httpx.MockTransport(handler))
    await asyncio.gather(*(client.fetch_fixtures() for _ in range(3 * MAX_CONCURRENT_REQUESTS)))
    await client.aclose()

    assert peak == MAX_CONCURRENT_REQUESTS
